#!/usr/bin/env bash
set -euo pipefail
[[ "$ACCEPT_MINECRAFT_EULA" == TRUE ]] || { echo "Minecraft EULA not accepted"; exit 1; }
[[ -n "$RCLONE_CONFIG_B64" ]] || { echo "RCLONE_CONFIG_B64 missing; refuse ephemeral world"; exit 1; }
[[ -n "$MC_CONTROL_TOKEN" ]] || { echo "MC_CONTROL_TOKEN missing"; exit 1; }
[[ -n "$TUNNEL_PRIVATE_KEY_B64" ]] || { echo "VPS_SSH_KEY_B64 missing"; exit 1; }
echo "RUNNER CPU=$(nproc), RAM=$(free -h | awk '/^Mem:/ {print $2}')"
sudo apt-get install -y rclone
mkdir -p "$HOME/.config/rclone" server "$RUNNER_TEMP/mc-snapshot" "$HOME/.ssh"
printf '%s' "$RCLONE_CONFIG_B64" | base64 -d > "$HOME/.config/rclone/rclone.conf"
chmod 600 "$HOME/.config/rclone/rclone.conf"
rclone lsd "$DRIVE_BASE" >/dev/null
python3 scripts/desired_state.py check --drive "$DRIVE_BASE" || {
  echo 'SERVER_STOPPED_BY_OWNER'; exit 0;
}
python3 scripts/world_store.py restore --server server --temp "$RUNNER_TEMP" \
  --drive "$DRIVE_BASE" --backup "${RESTORE_BACKUP:-}"

python3 scripts/desired_state.py check --drive "$DRIVE_BASE" || {
  echo 'OWNER_STOPPED_DURING_RESTORE'; exit 0;
}
AGENT="minecraft-hosting/1.0 (https://github.com/$GITHUB_REPOSITORY)"
BUILDS="https://fill.papermc.io/v3/projects/paper/versions/26.3/builds"
JSON=$(curl -fsSL --retry 3 -H "User-Agent: $AGENT" "$BUILDS")
URL=$(jq -er '[.[] | select(.channel == "STABLE")][0].downloads."server:default".url' <<< "$JSON")
SHA=$(jq -er '[.[] | select(.channel == "STABLE")][0].downloads."server:default".checksums.sha256' <<< "$JSON")
curl -fsSL --retry 3 -H "User-Agent: $AGENT" -o server/paper.jar "$URL"
echo "$SHA  server/paper.jar" | sha256sum -c -
echo eula=true > server/eula.txt
if [[ ! -f server/server.properties ]]; then
  cat > server/server.properties <<'PROPS'
server-ip=127.0.0.1
server-port=25565
online-mode=true
max-players=20
view-distance=8
simulation-distance=6
motd=Minecraft GitHub Hosting
enable-rcon=false
PROPS
fi
sed -i -e 's/^server-ip=.*/server-ip=127.0.0.1/' -e 's/^server-port=.*/server-port=25565/' server/server.properties

KEYFILE="$RUNNER_TEMP/mc-tunnel.key"
printf '%s' "$TUNNEL_PRIVATE_KEY_B64" | base64 -d > "$KEYFILE"
chmod 600 "$KEYFILE"
ssh-keygen -y -P '' -f "$KEYFILE" >/dev/null
ssh-keyscan -T 10 -t ed25519 "$VPS_HOST" > "$HOME/.ssh/known_hosts" 2>/dev/null
chmod 600 "$HOME/.ssh/known_hosts"
FP=$(ssh-keygen -lf "$HOME/.ssh/known_hosts" | awk '{print $2}')
[[ "$FP" == "$VPS_HOST_FINGERPRINT" ]] || { echo "SSH host fingerprint mismatch"; exit 1; }

mkfifo server/console.fifo
exec 3<>server/console.fifo
PAPER_PID=''
SSH_PID=''
CONTROL_PID=''
PAPER_STARTED=false
ROLLOVER_READY=false
dispatch_next() {
  python3 scripts/desired_state.py check --drive "$DRIVE_BASE" || {
    echo 'OWNER_STOPPED_NO_RESTART'; return 0;
  }
  [[ -n "$GH_TOKEN" && -n "$GITHUB_REPOSITORY" && -n "$WORKFLOW_REF" ]] || {
    echo 'NO_GITHUB_WORKFLOW_TOKEN'; return 1;
  }
  for attempt in 1 2 3; do
    python3 scripts/desired_state.py check --drive "$DRIVE_BASE" || {
      echo 'OWNER_STOPPED_NO_RESTART'; return 0;
    }
    if gh workflow run minecraft-hosting.yml -R "$GITHUB_REPOSITORY" --ref "$WORKFLOW_REF"; then
      echo 'SUCCESSOR_WORKFLOW_DISPATCHED'
      return 0
    fi
    sleep 8
  done
  echo 'SUCCESSOR_WORKFLOW_DISPATCH_FAILED'
  return 1
}
backup() {
  local kind="${1:-periodic}" snapshot_rc=0
  echo "DRIVE_BACKUP_START type=$kind at=$(date -u +%Y%m%dT%H%M%SZ)"
  python3 scripts/world_store.py check-space --source server --temp "$RUNNER_TEMP" || return 1
  if [[ -n "$PAPER_PID" ]] && kill -0 "$PAPER_PID" 2>/dev/null; then
    printf 'save-off\nsave-all flush\n' >&3
    sleep 5
  fi
  rsync -a --delete --exclude paper.jar --exclude console.fifo --exclude server.log \
    server/ "$RUNNER_TEMP/mc-snapshot/" || snapshot_rc=1
  if [[ -n "$PAPER_PID" ]] && kill -0 "$PAPER_PID" 2>/dev/null; then
    printf 'save-on\n' >&3 || :
  fi
  [[ "$snapshot_rc" -eq 0 ]] || { echo 'SNAPSHOT_COPY_FAILED'; return 1; }
  python3 scripts/world_store.py backup --source "$RUNNER_TEMP/mc-snapshot" \
    --temp "$RUNNER_TEMP" --drive "$DRIVE_BASE" --run-id "$GITHUB_RUN_ID"
  echo "DRIVE_BACKUP_OK type=$kind"
}
cleanup() {
  local rc=$?
  trap - EXIT TERM INT
  set +e
  if [[ -n "$PAPER_PID" ]] && kill -0 "$PAPER_PID" 2>/dev/null; then
    printf 'stop\n' >&3
    for i in $(seq 1 45); do
      kill -0 "$PAPER_PID" 2>/dev/null || break
      sleep 1
    done
    kill "$PAPER_PID" 2>/dev/null || :
    wait "$PAPER_PID" 2>/dev/null || :
  fi
  if [[ "$PAPER_STARTED" == true ]]; then
    backup final || { echo 'FINAL_BACKUP_FAILED'; rc=1; }
  fi
  [[ -n "$CONTROL_PID" ]] && kill "$CONTROL_PID" 2>/dev/null
  [[ -n "$SSH_PID" ]] && kill "$SSH_PID" 2>/dev/null
  exec 3>&-
  rm -f "$KEYFILE"
  if [[ "$rc" -eq 0 && "$ROLLOVER_READY" == true &&
        ! -f "$RUNNER_TEMP/mc-stop-requested" ]]; then
    dispatch_next || rc=1
  else
    echo 'NO_AUTOMATIC_NEXT_JOB'
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python3 scripts/desired_state.py check --drive "$DRIVE_BASE" || {
  echo 'OWNER_STOPPED_BEFORE_START'; exit 0;
}

(
  cd server
  java -Xms2G -Xmx12G -XX:+UseG1GC -XX:+ParallelRefProcEnabled \
    -XX:MaxGCPauseMillis=200 -jar paper.jar --nogui \
    < console.fifo > server.log 2>&1
) &
PAPER_PID=$!
PAPER_STARTED=true
python3 scripts/runner_console.py --fifo server/console.fifo --log server/server.log --paper-pid "$PAPER_PID" &
CONTROL_PID=$!
ssh -NT -i "$KEYFILE" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o PasswordAuthentication=no -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -R 127.0.0.1:25566:127.0.0.1:25565 \
  -R 127.0.0.1:25567:127.0.0.1:25567 \
  "${VPS_USER}@${VPS_HOST}" &
SSH_PID=$!
sleep 4
kill -0 "$SSH_PID" || { echo 'Tunnel failed'; exit 1; }
ready=false
for i in $(seq 1 120); do
  if grep -Fq 'Done (' server/server.log; then ready=true; break; fi
  kill -0 "$PAPER_PID" || { echo 'Paper exited'; tail -n 60 server/server.log; exit 1; }
  sleep 2
done
[[ "$ready" == true ]] || { echo 'Paper startup timeout'; exit 1; }
echo "MINECRAFT_READY address=$VPS_HOST:25565 heap=12G"
deadline=$((SECONDS + 18000))
next_backup=$((SECONDS + 900))
next_state_check=$((SECONDS + 30))
while (( SECONDS < deadline )); do
  sleep 5
  if ! kill -0 "$PAPER_PID"; then
    if [[ -f "$RUNNER_TEMP/mc-restart-requested" ]]; then
      ROLLOVER_READY=true
      echo 'CONTROLLED_RESTART_REQUESTED'
      break
    fi
    [[ -f "$RUNNER_TEMP/mc-stop-requested" ]] && {
      echo 'MANUAL_STOP_REQUESTED'; break;
    }
    echo 'Paper stopped unexpectedly'; exit 1
  fi
  kill -0 "$SSH_PID" || { echo 'Tunnel disconnected'; exit 1; }
  if (( SECONDS >= next_state_check )); then
    python3 scripts/desired_state.py check --drive "$DRIVE_BASE" || {
      echo 'OWNER_REQUESTED_STOP'
      touch "$RUNNER_TEMP/mc-stop-requested"
      break
    }
    next_state_check=$((SECONDS + 30))
  fi
  if (( SECONDS >= next_backup )); then
    backup periodic || echo "PERIODIC_BACKUP_FAILED; retry on next interval"
    next_backup=$((SECONDS + 900))
  fi
done
ROLLOVER_READY=true
echo 'Session complete; graceful shutdown and final Drive snapshot.'
