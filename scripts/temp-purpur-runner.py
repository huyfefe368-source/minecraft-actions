#!/usr/bin/env python3
"""Temporary, manually triggered Minecraft dev playtest on GitHub Actions."""
import hashlib, json, os, re, signal, socket, subprocess, sys, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from temp_mc_state import inventory,verify
from temp_mc_merge import merge_trees
from collections import deque
from pathlib import Path

VPS="160.191.51.148"
REMOTE="mcghplaytest@"+VPS
BASE=Path(os.environ["RUNNER_TEMP"])/"purpur-playtest"
SERVER=BASE/"server"
XFER=BASE/"transfer"
KEY=BASE/"ssh_key"
KNOWN=BASE/"known_hosts"
mc=None
tunnel=None
forwarded_port=None
stop_requested=threading.Event()
checkpoint_lock=threading.Lock()
last_snapshot=None
phase="starting"
cleanup_deadline=None
base_manifest={}
pending_snapshots=[]
server_requests=deque()
server_desired=True
sync_restart_pending=False
sync_state={"state":"idle","conflicts":[],"interval":5}

class CheckpointInterrupted(Exception):pass

def check_control():
    if phase in ('checkpoint','starting') and (stop_requested.is_set() or (BASE/'stop-requested').exists()):
        stop_requested.set()
        raise CheckpointInterrupted('Stop requested; reserve time for final snapshot')
    if cleanup_deadline is not None and time.monotonic()>=cleanup_deadline:
        raise subprocess.TimeoutExpired('final snapshot cleanup budget',240)

def note(*a): print("[PURPUR 1.21.11]",*a,flush=True)

def run(args,seconds=150,capture=False):
    check_control()
    args=[str(a) for a in args]
    deadline=time.monotonic()+seconds
    if cleanup_deadline is not None:deadline=min(deadline,cleanup_deadline)
    proc=subprocess.Popen(args,text=True,start_new_session=True,
        stdout=subprocess.PIPE if capture else None,stderr=subprocess.PIPE if capture else None)
    try:
        while True:
            check_control()
            remaining=deadline-time.monotonic()
            if remaining<=0:raise subprocess.TimeoutExpired(args,seconds)
            try:
                stdout,stderr=proc.communicate(timeout=min(0.25,remaining))
                break
            except subprocess.TimeoutExpired:pass
        if proc.returncode:raise subprocess.CalledProcessError(proc.returncode,args,stdout,stderr)
        return stdout.strip() if capture else ''
    except BaseException:
        if proc.poll() is None:
            os.killpg(proc.pid,signal.SIGTERM)
            try:proc.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL);proc.communicate()
        raise

def opts():
    return ["-i",str(KEY),"-o","BatchMode=yes","-o","IdentitiesOnly=yes",
            "-o","StrictHostKeyChecking=yes","-o","UserKnownHostsFile="+str(KNOWN),
            "-o","ConnectTimeout=12","-o","ServerAliveInterval=25",
            "-o","ServerAliveCountMax=3"]

def ssh(cmd,seconds=150):
    return run(["ssh",*opts(),REMOTE,cmd],seconds=seconds,capture=True)

def rsync(src,dst,follow=False,seconds=300):
    arg=["rsync","-a","--checksum","--no-owner","--no-group"]
    if follow:arg.append("-L")
    if "/tree-incoming/" in str(dst) and str(dst).endswith("/server/"):
        arg.append("--copy-dest=/srv/mc-temp12111/tree-repo/server")
    run(arg+["-e","ssh "+" ".join(opts()),str(src),str(dst)],seconds=seconds)

def digest(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def setup():
    BASE.mkdir(parents=True,exist_ok=True)
    SERVER.mkdir(exist_ok=True)
    XFER.mkdir(exist_ok=True)
    priv=Path(os.environ["TEMP_PURPUR_KEY_FILE"]).read_text() if os.getenv("TEMP_PURPUR_KEY_FILE") else os.getenv("TEMP_PURPUR_SSH_KEY","")
    if not priv.startswith("-----BEGIN OPENSSH PRIVATE KEY-----"):
        raise RuntimeError("Missing TEMP_PURPUR_SSH_KEY GitHub Actions secret")
    KEY.write_text(priv.rstrip()+"\n")
    KEY.chmod(0o600)
    KNOWN.write_bytes((Path(__file__).parent/"temp-mc-known_hosts").read_bytes())
    KNOWN.chmod(0o600)
    note("SSH check:",ssh("echo VPS_SSH_OK",seconds=30))

def restore():
    global base_manifest
    note("Restoring full server tree from private Git repository via VPS")
    note(ssh("sudo -n /opt/mc-temp12111/restore-tree.sh",seconds=240))
    rsync(REMOTE+":/srv/mc-temp12111/tree-repo/server/",str(SERVER)+"/",seconds=300)
    base_manifest=inventory(SERVER,guard=check_control)


def prepare():
    jar=SERVER/"server.jar"
    if not jar.is_file():
        note("Downloading Purpur 1.21.11 build 2568")
        run(["curl","-fsSL","--retry","4","-o",str(jar),
             "https://api.purpurmc.org/v2/purpur/1.21.11/2568/download"],seconds=180)
        run(["unzip","-tqq",str(jar)],seconds=40)
    properties=SERVER/"server.properties"
    if not properties.exists():
        properties.write_text('server-ip=127.0.0.1\nserver-port=25565\nonline-mode=true\nenable-rcon=false\nenable-query=false\n')
    if "eula=true" not in (SERVER/"eula.txt").read_text().lower():
        raise RuntimeError("Minecraft EULA missing; must be accepted in initial server state")
    note("Java version:")
    run(["java","-version"],seconds=12)

def send(command):
    if mc is not None and mc.poll() is None:
        try:
            mc.stdin.write(command+"\n")
            mc.stdin.flush()
        except (OSError,ValueError):pass

def startup_ready(log,offset):
    with log.open("rb") as stream:
        stream.seek(offset);message=stream.read().decode(errors="replace")
    return bool(re.search(r"Done \([0-9.]+s\)!",message))

def minecraft_port():
    port=25565
    properties=SERVER/'server.properties'
    if properties.exists():
        for line in properties.read_text().splitlines():
            if line.startswith('server-port='):port=int(line.split('=',1)[1])
    if not 1<=port<=65535:raise ValueError('Invalid Minecraft port')
    return port

def open_tunnel():
    global tunnel,forwarded_port
    forwarded_port=minecraft_port()
    tunnel=subprocess.Popen(["ssh",*opts(),"-N","-T","-o","ExitOnForwardFailure=yes",
        "-R","127.0.0.1:25578:127.0.0.1:"+str(forwarded_port),
        "-R","127.0.0.1:25579:127.0.0.1:25579",REMOTE],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    time.sleep(3)
    if tunnel.poll() is not None:
        raise RuntimeError("Reverse tunnel failed: "+
                           tunnel.stderr.read().decode(errors="replace")[-450:])

def start():
    global mc,phase
    phase="starting"
    if forwarded_port!=minecraft_port():
        if tunnel is not None and tunnel.poll() is None:
            tunnel.terminate();tunnel.wait(timeout=10)
        open_tunnel()
    (SERVER/"logs").mkdir(exist_ok=True)
    log=SERVER/"logs"/"actions-console.log"
    log_offset=log.stat().st_size if log.exists() else 0
    with log.open("a",encoding="utf8",buffering=1) as out:
        mc=subprocess.Popen(["java","-Xms2G","-Xmx12G","-XX:ActiveProcessorCount=4",
                             "-XX:+UseG1GC","-XX:+ParallelRefProcEnabled",
                             "-XX:MaxGCPauseMillis=200","-jar","server.jar","nogui"],
                            cwd=SERVER,stdin=subprocess.PIPE,stdout=out,
                            stderr=subprocess.STDOUT,text=True,bufsize=1)
    deadline=time.monotonic()+300
    while time.monotonic()<deadline:
        if mc.poll() is not None:
            raise RuntimeError("Minecraft crashed: " +
                "\n".join(log.read_text(errors="replace").splitlines()[-14:]))
        if startup_ready(log,log_offset):
            note("SERVER READY. Connect Minecraft Java 1.21.11 to",VPS+":25577")
            return
        if (BASE/"stop-requested").exists() or stop_requested.is_set():
            stop_requested.set()
            mc.terminate()
            mc.wait(timeout=70)
            return
        time.sleep(1)
    raise RuntimeError("Minecraft did not start within 5 minutes")

def shutdown():
    if mc is not None:
        if mc.poll() is None:
            note("Saving all chunks to disk and stopping Java...")
            send("save-all flush")
            time.sleep(8)
            send("stop")
            try:mc.wait(timeout=70)
            except subprocess.TimeoutExpired:
                mc.terminate()
                try:mc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    mc.kill();mc.wait(timeout=15)
        note("Java process exited:",mc.returncode)

def checkpoint(final=False):
    global last_snapshot,phase,base_manifest,sync_restart_pending
    sync_state["state"]="syncing"
    with checkpoint_lock:
        phase="saving" if final else "checkpoint"
        snap=BASE/"snapshot"
        snap.mkdir(exist_ok=True)
        live=mc is not None and mc.poll() is None
        if live:
            send("save-off")
            # Wait for the actual Minecraft acknowledgement, never a fixed guess.
            log=SERVER/"logs/actions-console.log"
            offset=log.stat().st_size
            send("save-all flush")
            limit=time.monotonic()+60
            while time.monotonic()<limit:
                try:check_control()
                except CheckpointInterrupted:
                    send('save-on')
                    raise
                with log.open() as stream:
                    stream.seek(offset); message=stream.read()
                if "Saved the game" in message or "All dimensions are saved" in message:break
                if mc.poll() is not None:raise RuntimeError("Java exited during checkpoint")
                time.sleep(0.3)
            else:
                send("save-on")
                raise RuntimeError("Minecraft did not acknowledge save-all flush")
        try:
            run(["rsync","-a","--checksum","--delete",str(SERVER)+"/",str(snap)+"/"],seconds=90)
        finally:
            if live:send("save-on")
        # Track otherwise-empty folders in Git without archives.
        for directory in sorted(snap.rglob('*'),reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):(directory/'.gitkeep').touch()
        files=inventory(snap,guard=check_control)
        if "world/level.dat" not in files:raise RuntimeError("No world; refusing empty checkpoint")
        ident=os.environ["GITHUB_RUN_ID"]+"-"+str(time.time_ns())
        dest="/srv/mc-temp12111/tree-incoming/"+ident
        ssh("mkdir -p "+dest+"/server",seconds=30)
        rsync(str(snap)+"/",REMOTE+":"+dest+"/server/",seconds=180)
        manifest=XFER/"manifest.json"
        manifest.write_text(json.dumps({"id":ident,"final":final,"files":files,"base":base_manifest,"pending":list(pending_snapshots)}))
        rsync(manifest,REMOTE+":"+dest+"/manifest.json",seconds=30)
        pending_snapshots.append(ident)
        ssh("touch "+dest+"/READY && sudo -n /opt/mc-temp12111/publish-tree",seconds=20)
        ack={}
        ack_deadline=time.monotonic()+(120 if not final else 180)
        while time.monotonic()<ack_deadline:
            raw=ssh("if test -f /srv/mc-temp12111/tree-acks/"+ident+".json; then cat /srv/mc-temp12111/tree-acks/"+ident+".json; else echo '{}'; fi",seconds=15)
            ack=json.loads(raw)
            if ack.get('snapshot')==ident:break
            time.sleep(1)
        else:raise RuntimeError('Sync acknowledgement timeout; VPS retains snapshot')
        if ack.get('error'):raise RuntimeError(ack['error'])
        if ack.get('applied_remote') and not final:
            sync_state['state']='applying'
            restart=mc is not None and mc.poll() is None
            if restart:
                sync_restart_pending=server_desired
                shutdown()
            incoming=BASE/'inbound';incoming.mkdir(exist_ok=True)
            run(['rsync','-a','--checksum','--delete','-e','ssh '+ ' '.join(opts()),REMOTE+':/srv/mc-temp12111/tree-repo/server/',str(incoming)+'/'],seconds=120)
            target=BASE/'apply-tree'
            import shutil
            if target.exists():shutil.rmtree(target)
            evidence=BASE/'apply-conflicts'/ident
            result=merge_trees(files,SERVER,incoming,target,evidence)
            run(['rsync','-a','--checksum','--delete',str(target)+'/',str(SERVER)+'/'],seconds=60)
            verify(SERVER,result['files'])
            if result['conflicts'] and evidence.exists():
                shutil.copytree(evidence,SERVER/'.sync-conflicts'/ident,dirs_exist_ok=True)
        if not final:resume_minecraft()
        base_manifest=ack['files']
        pending_snapshots.clear()
        sync_state.update(state='idle',last_sync=time.time(),conflicts=ack.get('conflicts',[]),commit=ack['commit'])
        last_snapshot=ident
        note("FULL_TREE_DURABLE_ON_VPS",ident,"final=",final)
        phase="stopped" if final or mc is None or mc.poll() is not None else "running"

def resume_minecraft():
    global sync_restart_pending,phase
    if sync_restart_pending and server_desired and not stop_requested.is_set():
        if mc is None or mc.poll() is not None:
            prepare();start();phase='running'
        sync_restart_pending=False

def handle_server_action(action):
    global server_desired,sync_restart_pending,phase
    server_desired=action!='stop'
    sync_restart_pending=action in ('start','restart')
    try:
        if action in ('stop','restart'):
            shutdown();phase='stopped';checkpoint()
        if action in ('start','restart'):resume_minecraft()
        return True
    except CheckpointInterrupted:raise
    except Exception as error:
        sync_state.update(state='error',error=str(error))
        note('MINECRAFT_ACTION_RETRY',action,str(error))
        return False


class Console(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,code,data):
        body=json.dumps(data).encode();self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        log=SERVER/"logs/actions-console.log"
        lines=[]
        if log.exists():
            with log.open('rb') as f:
                f.seek(max(0,log.stat().st_size-48000));lines=f.read().decode(errors='replace').splitlines()[-250:]
        self.reply(200,{"phase":phase,"run_id":os.environ['GITHUB_RUN_ID'],"last_snapshot":last_snapshot,"log":lines,"minecraft_running":mc is not None and mc.poll() is None,"sync":sync_state})
    def do_POST(self):
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size))
            if data.get('op')=='job-stop':
                stop_requested.set();return self.reply(200,{'ok':True})
            if data.get('op')=='server-control':
                action=data.get('action')
                if action not in ('start','restart','stop'):raise ValueError('Invalid Minecraft action')
                server_requests.append(action);return self.reply(202,{'ok':True})
            command=data.get('command','').strip()
            if not command or len(command)>1000 or '\n' in command or '\r' in command:raise ValueError('One Minecraft command required')
            if command.lower()=='stop':server_requests.append('stop')
            else:send(command)
            self.reply(200,{"ok":True})
        except (ValueError,TypeError,AttributeError) as e:self.reply(400,{"error":str(e)})

def on_signal(sig,frame):
    stop_requested.set()


def main():
    if len(sys.argv)!=2 or not re.fullmatch("[0-9]{1,3}",sys.argv[1]):
        raise RuntimeError("Usage: runner.py DURATION_MINUTES")
    minutes=int(sys.argv[1])
    if not 10<=minutes<=330:raise RuntimeError("Duration must be 10..330 minutes")
    signal.signal(signal.SIGTERM,on_signal)
    signal.signal(signal.SIGINT,on_signal)
    global phase,cleanup_deadline,server_desired
    http=None
    result=0
    try:
        setup();restore();prepare()
        check_control()
        http=ThreadingHTTPServer(('127.0.0.1',25579),Console)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        open_tunnel()
        start();phase="running"
        if not stop_requested.is_set():
            try:checkpoint()
            except CheckpointInterrupted:raise
            except Exception as error:
                sync_state.update(state='error',error=str(error));note('INITIAL_SYNC_RETRY',str(error))
        until=time.monotonic()+minutes*60
        next_save=time.monotonic()+5
        while time.monotonic()<until and not stop_requested.wait(0.5):
            if (BASE/"stop-requested").exists():break
            if tunnel.poll() is not None:
                note('Job tunnel ended');break
            if server_requests:
                handle_server_action(server_requests.popleft())
                next_save=time.monotonic()+5
            if mc is not None and mc.poll() is not None:phase='stopped'
            if time.monotonic()>=next_save:
                try:checkpoint()
                except Exception as error:
                    sync_state.update(state="error",error=str(error));note("CHECKPOINT_RETRY",str(error))
                next_save=time.monotonic()+5
    except CheckpointInterrupted:
        note('Checkpoint interrupted for a normal early stop')
    except Exception as error:
        result=1;note("SESSION_ERROR",str(error))
    finally:
        phase="stopping"
        # Reserve 40 seconds for the Actions finalizer and VM cleanup.
        cleanup_deadline=time.monotonic()+240
        shutdown()
        if mc is not None and mc.poll() is None:
            raise RuntimeError("Java is still alive; refusing inconsistent final snapshot")
        try:
            if mc is not None:checkpoint(final=True)
            else:
                note('Stopped before Java launch; keep previous complete server tree')
                if stop_requested.is_set() and KEY.exists():
                    ssh('mkdir -p /srv/mc-temp12111/completed-without-launch && touch /srv/mc-temp12111/completed-without-launch/'+os.environ['GITHUB_RUN_ID'],seconds=10)
        except Exception as error:
            phase="backup-failed";note("FINAL_TREE_BACKUP_FAILED",str(error));result=1
        finally:
            if tunnel is not None and tunnel.poll() is None:tunnel.terminate()
        if http is not None:http.shutdown()
        BASE.mkdir(parents=True,exist_ok=True)
        (BASE/"result").write_text(str(result))
        note("Done; full tree retained on VPS; publisher retries Git push independently")
    return result

if __name__=="__main__":
    sys.exit(main())
