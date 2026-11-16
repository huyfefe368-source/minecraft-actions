#!/usr/bin/env python3
"""Temporary, manually triggered Minecraft dev playtest on GitHub Actions."""
import hashlib, json, os, re, signal, socket, subprocess, sys, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from temp_mc_state import inventory
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
stop_requested=threading.Event()
checkpoint_lock=threading.Lock()
last_snapshot=None
phase="starting"

def note(*a): print("[PURPUR 1.21.11]",*a,flush=True)

def run(args,seconds=150,capture=False):
    r=subprocess.run([str(a) for a in args],check=True,timeout=seconds,
                     text=True,capture_output=capture)
    return r.stdout.strip() if capture else ""

def opts():
    return ["-i",str(KEY),"-o","BatchMode=yes","-o","IdentitiesOnly=yes",
            "-o","StrictHostKeyChecking=yes","-o","UserKnownHostsFile="+str(KNOWN),
            "-o","ConnectTimeout=12","-o","ServerAliveInterval=25",
            "-o","ServerAliveCountMax=3"]

def ssh(cmd,seconds=150):
    return run(["ssh",*opts(),REMOTE,cmd],seconds=seconds,capture=True)

def rsync(src,dst,follow=False,seconds=300):
    arg=["rsync","-a","--no-owner","--no-group"]
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
    note("Restoring full server tree from private Git repository via VPS")
    note(ssh("sudo -n /opt/mc-temp12111/restore-tree.sh",seconds=240))
    rsync(REMOTE+":/srv/mc-temp12111/tree-repo/server/",str(SERVER)+"/",seconds=300)


def prepare():
    jar=SERVER/"server.jar"
    if not jar.is_file():
        note("Downloading Purpur 1.21.11 build 2568")
        run(["curl","-fsSL","--retry","4","-o",str(jar),
             "https://api.purpurmc.org/v2/purpur/1.21.11/2568/download"],seconds=180)
        run(["unzip","-tqq",str(jar)],seconds=40)
    properties=SERVER/"server.properties"
    lines=properties.read_text().splitlines() if properties.exists() else []
    override={"server-ip":"127.0.0.1","server-port":"25565","online-mode":"true",
              "enable-rcon":"false","enable-query":"false"}
    lines=[l for l in lines if not any(l.startswith(k+"=") for k in override)]
    properties.write_text("\n".join(lines+[k+"="+v for k,v in override.items()])+"\n")
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

def start():
    global mc,tunnel
    tunnel=subprocess.Popen(["ssh",*opts(),"-N","-T","-o","ExitOnForwardFailure=yes",
        "-R","127.0.0.1:25578:127.0.0.1:25565",
        "-R","127.0.0.1:25579:127.0.0.1:25579",REMOTE],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    time.sleep(3)
    if tunnel.poll() is not None:
        raise RuntimeError("Reverse tunnel failed: "+
                           tunnel.stderr.read().decode(errors="replace")[-450:])
    (SERVER/"logs").mkdir(exist_ok=True)
    log=SERVER/"logs"/"actions-console.log"
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
        try:
            with socket.create_connection(("127.0.0.1",25565),timeout=1):
                note("SERVER READY. Connect Minecraft Java 1.21.11 to",VPS+":25577")
                return
        except OSError:time.sleep(4)
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
    global last_snapshot,phase
    with checkpoint_lock:
        phase="saving" if final else "checkpoint"
        snap=BASE/"snapshot"
        snap.mkdir(exist_ok=True)
        if not final:
            send("save-off")
            # Wait for the actual Minecraft acknowledgement, never a fixed guess.
            log=SERVER/"logs/actions-console.log"
            offset=log.stat().st_size
            send("save-all flush")
            limit=time.monotonic()+60
            while time.monotonic()<limit:
                with log.open() as stream:
                    stream.seek(offset); message=stream.read()
                if "Saved the game" in message or "All dimensions are saved" in message:break
                if mc.poll() is not None:raise RuntimeError("Java exited during checkpoint")
                time.sleep(0.3)
            else:
                send("save-on")
                raise RuntimeError("Minecraft did not acknowledge save-all flush")
        try:
            run(["rsync","-a","--delete",str(SERVER)+"/",str(snap)+"/"],seconds=90)
        finally:
            if not final:send("save-on")
        files=inventory(snap)
        if "world/level.dat" not in files:raise RuntimeError("No world; refusing empty checkpoint")
        ident=os.environ["GITHUB_RUN_ID"]+"-"+str(time.time_ns())
        dest="/srv/mc-temp12111/tree-incoming/"+ident
        ssh("mkdir -p "+dest+"/server",seconds=30)
        rsync(str(snap)+"/",REMOTE+":"+dest+"/server/",seconds=180)
        manifest=XFER/"manifest.json"
        manifest.write_text(json.dumps({"id":ident,"final":final,"files":files}))
        rsync(manifest,REMOTE+":"+dest+"/manifest.json",seconds=30)
        ssh("touch "+dest+"/READY",seconds=20)
        last_snapshot=ident
        note("FULL_TREE_DURABLE_ON_VPS",ident,"final=",final)
        phase="stopped" if final else "running"

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
        self.reply(200,{"phase":phase,"run_id":os.environ['GITHUB_RUN_ID'],"last_snapshot":last_snapshot,"log":lines})
    def do_POST(self):
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size));command=data.get('command','').strip()
            if not command or len(command)>1000 or '\n' in command or '\r' in command:raise ValueError('One Minecraft command required')
            if command.lower()=='stop':stop_requested.set()
            else:send(command)
            self.reply(200,{"ok":True})
        except (ValueError,TypeError,AttributeError) as e:self.reply(400,{"error":str(e)})

def on_signal(sig,frame):
    stop_requested.set()


def main():
    if len(sys.argv)!=2 or not re.fullmatch("[0-9]{1,3}",sys.argv[1]):
        raise RuntimeError("Usage: runner.py DURATION_MINUTES")
    minutes=int(sys.argv[1])
    if not 10<=minutes<=270:raise RuntimeError("Duration must be 10..270 minutes")
    signal.signal(signal.SIGTERM,on_signal)
    signal.signal(signal.SIGINT,on_signal)
    setup();restore();prepare()
    global phase
    http=ThreadingHTTPServer(('127.0.0.1',25579),Console)
    threading.Thread(target=http.serve_forever,daemon=True).start()
    result=0
    try:
        start();phase="running"
        checkpoint()
        until=time.monotonic()+minutes*60
        next_save=time.monotonic()+60
        while time.monotonic()<until and not stop_requested.wait(1):
            if (BASE/"stop-requested").exists():break
            if mc.poll() is not None or tunnel.poll() is not None:
                note("Server or tunnel ended early");break
            if time.monotonic()>=next_save:
                try:checkpoint()
                except Exception as error:note("CHECKPOINT_RETRY",str(error))
                next_save=time.monotonic()+60
    except Exception as error:
        result=1;note("SESSION_ERROR",str(error))
    finally:
        phase="stopping"
        shutdown()
        if mc is not None and mc.poll() is None:
            raise RuntimeError("Java is still alive; refusing inconsistent final snapshot")
        try:checkpoint(final=True)
        except Exception as error:
            phase="backup-failed";note("FINAL_TREE_BACKUP_FAILED",str(error));result=1
        finally:
            if tunnel is not None and tunnel.poll() is None:tunnel.terminate()
        http.shutdown()
        (BASE/"result").write_text(str(result))
        note("Done; full tree retained on VPS; publisher retries Git push independently")
    return result

if __name__=="__main__":
    sys.exit(main())
