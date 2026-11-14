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
if [[ -n "${RESTORE_BACKUP:-}" ]]; then
  [[ "$RESTORE_BACKUP" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+\.tar\.gz$ ]] || { echo 'Invalid backup name'; exit 1; }
  rclone copyto "$DRIVE_BASE/backups/$RESTORE_BACKUP" "$RUNNER_TEMP/restore.tar.gz"
  if tar -tzf "$RUNNER_TEMP/restore.tar.gz" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
    echo 'Unsafe backup archive'; exit 1
  fi
  tar -xzf "$RUNNER_TEMP/restore.tar.gz" -C server --no-same-owner --no-same-permissions
  echo "RESTORED_BACKUP=$RESTORE_BACKUP"
elif rclone lsf "$DRIVE_BASE/live" >/dev/null 2>&1; then
  rclone sync "$DRIVE_BASE/live" server --transfers 4 --checkers 8
  echo DRIVE_RESTORE_OK
else
  echo DRIVE_NEW_WORLD
fi

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
backup() {
  local kind="${1:-periodic}" stamp archive ret
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  echo "DRIVE_BACKUP_START type=$kind time=$stamp"
  if [[ -n "$PAPER_PID" ]] && kill -0 "$PAPER_PID" 2>/dev/null; then
    printf 'save-off\nsave-all flush\n' >&3
    sleep 5
  fi
  ret=0
  rsync -a --delete --exclude paper.jar --exclude console.fifo --exclude server.log \
    server/ "$RUNNER_TEMP/mc-snapshot/" || ret=1
  if [[ -n "$PAPER_PID" ]] && kill -0 "$PAPER_PID" 2>/dev/null; then
    printf 'save-on\n' >&3 || :
  fi
  [[ "$ret" == 0 ]] || { echo 'SNAPSHOT_FAILED'; return 1; }
  archive="$RUNNER_TEMP/minecraft-$stamp.tar.gz"
  tar -czf "$archive" -C "$RUNNER_TEMP/mc-snapshot" .
  rclone copyto "$archive" "$DRIVE_BASE/backups/$stamp-$GITHUB_RUN_ID.tar.gz" --retries 3
  rm -f "$archive"
  rclone sync "$RUNNER_TEMP/mc-snapshot/" "$DRIVE_BASE/live" \
    --transfers 4 --checkers 8 --retries 3
  echo "DRIVE_BACKUP_OK type=$kind time=$stamp"
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
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

(
  cd server
  java -Xms2G -Xmx12G -XX:+UseG1GC -XX:+ParallelRefProcEnabled \
    -XX:MaxGCPauseMillis=200 -jar paper.jar --nogui \
    < console.fifo > server.log 2>&1
) &
PAPER_PID=$!
PAPER_STARTED=true
python3 scripts/runner_console.py --fifo server/console.fifo --log server/server.log &
CONTROL_PID=$!
ssh -NT -i "$KEYFILE" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o PasswordAuthentication=no -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -R 127.0.0.1:25566:127.0.0.1:25565 \
  -R 127.0.0.1:25567:127.0.0.1:25567 \
  "$VPS_USER$@VPS_HOST" &
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
while (( SECONDS < deadline )); do
  sleep 5
  if ! kill -0 "$PAPER_PID"; then
    [[ -f "$RUNNER_TEMP/mc-stop-requested" ]] && break
    echo 'Paper stopped unexpectedly'; exit 1
  fi
  kill -0 "$SSH_PID" || { echo 'Tunnel disconnected'; exit 1; }
  if (( SECONDS >= next_backup )); then
    backup periodic || echo "PERIODIC_BACKUP_FAILED; retry on next interval"
    next_backup=$((SECONDS + 900))
  fi
done
echo 'Session complete; graceful shutdown and final Drive snapshot.'
