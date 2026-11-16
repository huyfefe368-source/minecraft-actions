#!/usr/bin/env python3
"""Temporary, manually triggered Minecraft dev playtest on GitHub Actions."""
import hashlib, json, os, re, signal, socket, subprocess, sys, time
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
    priv=os.getenv("TEMP_PURPUR_SSH_KEY","")
    if not priv.startswith("-----BEGIN OPENSSH PRIVATE KEY-----"):
        raise RuntimeError("Missing TEMP_PURPUR_SSH_KEY GitHub Actions secret")
    KEY.write_text(priv.rstrip()+"\n")
    KEY.chmod(0o600)
    KNOWN.write_bytes((Path(__file__).parent/"temp-mc-known_hosts").read_bytes())
    KNOWN.chmod(0o600)
    note("SSH check:",ssh("echo VPS_SSH_OK",seconds=30))

def restore():
    note("Downloading the newest complete archive from the PRIVATE GitHub repo via VPS")
    status=ssh("sudo -n /opt/mc-temp12111/restore-latest.sh",seconds=240)
    note(status)
    if "NO_PREVIOUS_BACKUP" in status:return
    if "RESTORED_FROM_GITHUB_RELEASE" not in status:
        raise RuntimeError("Private Release recovery did not complete")
    remote="/srv/mc-temp12111/cache/"
    arc=XFER/"current.tar.zst"
    manifest=XFER/"current.manifest.json"
    rsync(REMOTE+":"+remote+"current.tar.zst",arc,follow=True)
    rsync(REMOTE+":"+remote+"current.manifest.json",manifest,follow=True)
    info=json.loads(manifest.read_text())
    if digest(arc)!=info["sha256"] or arc.stat().st_size!=info["size_bytes"]:
        raise RuntimeError("Release archive checksum/size mismatch — refusing restore")
    note("Full snapshot SHA256 verified",info["sha256"])
    with arc.open("rb") as f:
        z=subprocess.Popen(["zstd","-dc"],stdin=f,stdout=subprocess.PIPE)
        try:
            run_tar=subprocess.run(["tar","-xf","-","-C",str(SERVER)],
                                   stdin=z.stdout,check=True)
        finally:
            z.stdout.close()
            if z.wait()!=0:raise RuntimeError("Corrupted compressed archive")

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
        "-R","127.0.0.1:25578:127.0.0.1:25565",REMOTE],
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
                except subprocess.TimeoutExpired:mc.kill()
        note("Java process exited:",mc.returncode)

def backup():
    if not (SERVER/"world"/"level.dat").is_file():
        note("No generated world yet: keeping the last safe backup")
        return
    name="mc-state-"+str(int(os.environ["GITHUB_RUN_ID"]))+"-"+str(int(os.environ.get("GITHUB_RUN_ATTEMPT","1")))+".tar.zst"
    archive=XFER/name
    note("Archiving FULL stopped server folder (includes world, plugins, configs, jar and logs)")
    with archive.open("wb") as f:
        tar=subprocess.Popen(["tar","-C",str(SERVER),"-cf","-","."],stdout=subprocess.PIPE)
        z=subprocess.Popen(["zstd","-T2","-5","-q"],stdin=tar.stdout,stdout=f)
        tar.stdout.close()
        rz=z.wait()
        rt=tar.wait()
        if rt!=0 or rz!=0:raise RuntimeError("Backup tar/zstd failed")
    run(["zstd","-tq",str(archive)],seconds=120)
    if archive.stat().st_size>=2_000_000_000:
        raise RuntimeError("Archive >=2GB cannot be a single GitHub Release asset")
    checksum=digest(archive)
    sha_file=XFER/(name+".sha256")
    sha_file.write_text(checksum+"\n")
    target="/srv/mc-temp12111/incoming/"
    rsync(archive,REMOTE+":"+target+name+".part",seconds=400)
    rsync(sha_file,REMOTE+":"+target+name+".sha256",seconds=30)
    ssh("mv "+target+name+".part "+target+name,seconds=20)
    ssh("touch "+target+name+".ready",seconds=20)
    note("Sending validated full archive into PRIVATE GitHub Release...")
    receipt=ssh("sudo -n /opt/mc-temp12111/publish-pending.sh",seconds=650)
    note(receipt)
    if "GITHUB_RELEASE_PUBLISHED "+name not in receipt:
        raise RuntimeError("Publish incomplete; VPS retry timer will retry the retained archive")
    note("BACKUP PUBLISHED and retained for next manual Actions run")

def on_signal(sig,frame):
    raise KeyboardInterrupt

def main():
    if len(sys.argv)!=2 or not re.fullmatch("[0-9]{1,3}",sys.argv[1]):
        raise RuntimeError("Usage: runner.py DURATION_MINUTES")
    minutes=int(sys.argv[1])
    if not 10<=minutes<=270:raise RuntimeError("Duration must be 10..270 minutes")
    signal.signal(signal.SIGTERM,on_signal)
    signal.signal(signal.SIGINT,on_signal)
    setup();restore();prepare()
    try:
        start()
        until=time.monotonic()+minutes*60
        next_info=time.monotonic()+60
        while time.monotonic()<until:
            if mc.poll() is not None or tunnel.poll() is not None:
                note("Server or tunnel ended early");break
            if time.monotonic()>next_info:
                note("Playtest online; remaining minutes",round((until-time.monotonic())/60,1))
                next_info=time.monotonic()+60
            time.sleep(5)
    except KeyboardInterrupt:
        note("Cancellation requested; attempting graceful backup")
    finally:
        shutdown()
        try:backup()
        finally:
            if tunnel is not None and tunnel.poll() is None:tunnel.terminate()
        note("Done. Resume from the private backup by manually dispatching the workflow")

if __name__=="__main__":
    main()
