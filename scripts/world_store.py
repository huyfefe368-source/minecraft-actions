#!/usr/bin/env python3
"""Drive backup and restore with versioned, SHA-256 verified archives.

World data only resides on the runner while Paper is active. All metadata writes
are last; an interrupted live sync never becomes the default restore source.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time

RESERVE = 2 * 1024**3
SKIP = {"paper.jar", "console.fifo", "server.log"}
BACKUP_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9]+\.tar\.gz$")
class SnapshotError(RuntimeError): pass

def sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def source_size(path: Path) -> int:
    total=0
    for entry in Path(path).rglob("*"):
        if entry.is_symlink():raise SnapshotError("World contains symlinks")
        if entry.is_file() and entry.name not in SKIP:
            total+=entry.stat().st_size
    return total

def required_free_bytes(size: int,free_bytes: int | None = None) -> int:
    needed=size * 2 + RESERVE
    if free_bytes is not None and free_bytes < needed:
        raise SnapshotError("Not enough temporary runner SSD for a safe snapshot")
    return needed

def archive_world(source: Path, destination: Path):
    source=Path(source)
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(destination,"w:gz") as tf:
        for item in sorted(source.rglob("*")):
            if item.name in SKIP:continue
            if item.is_symlink():raise SnapshotError("Refusing symlink in world")
            if not (item.is_file() or item.is_dir()):raise SnapshotError("Refusing special world file")
            rel=item.relative_to(source).as_posix()
            tf.add(item,arcname=rel,recursive=False)

def validate_and_extract(archive: Path,target: Path,expected_sha: str):
    if not re.fullmatch(r"[0-9a-f]{64}",expected_sha):
        raise SnapshotError("Malformed expected checksum")
    if sha256(archive)!=expected_sha:
        raise SnapshotError("Backup checksum mismatch")
    with tarfile.open(archive,"r:gz") as tf:
        for member in tf.getmembers():
            path=Path(member.name)
            if (path.is_absolute() or not member.name or
                any(p in (".","..","") for p in path.parts) or
                not (member.isdir() or member.isfile())):
                raise SnapshotError("Unsafe world archive member")
        Path(target).mkdir(parents=True,exist_ok=True)
        tf.extractall(target,filter="data")

def backup_world(server: Path, store, tmp: Path, run_id: int, stamp: str | None = None,
                 free_bytes: int | None = None, snapshot_ready: bool = False) -> dict:
    source=Path(server)
    tmp=Path(tmp)
    tmp.mkdir(parents=True,exist_ok=True)
    actual_free=free_bytes if free_bytes is not None else shutil.disk_usage(tmp).free
    size=source_size(source)
    minimum=(size + RESERVE) if snapshot_ready else required_free_bytes(size)
    if actual_free < minimum:
        raise SnapshotError("Temporary runner disk headroom too low")
    stamp=stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z",stamp):
        raise SnapshotError("Invalid backup timestamp")
    name=f"{stamp}-{int(run_id)}.tar.gz"
    snapshot=source
    if not snapshot_ready:
        snap=tmp/"frozen-world"
        if snap.exists():
            shutil.rmtree(snap)
        shutil.copytree(source,snap,symlinks=False,ignore=shutil.ignore_patterns(*SKIP))
        snapshot=snap
    archive=tmp/name
    archive_world(snapshot,archive)
    manifest={"backup_name":name,"created_at":stamp,"sha256":sha256(archive),
              "size_bytes":archive.stat().st_size,"run_id":int(run_id),"verified":True}
    try:
        store.upload(archive,"backups/"+name)
        if store.hash_remote("backups/"+name) != manifest["sha256"]:
            raise SnapshotError("Remote uploaded archive failed SHA-256 verification")
        manifest_json=json.dumps(manifest,sort_keys=True).encode()
        store.put("backups/"+name+".json",manifest_json)
        store.sync(snapshot,"live")
        store.check(snapshot,"live")
        # This is the only commit marker. If live sync failed, the old marker remains.
        store.put("metadata/current.json",manifest_json)
    except (OSError,IOError,subprocess.CalledProcessError) as exc:
        raise SnapshotError("Backup failed; last known good checkpoint remains") from None
    return manifest

def restore_world(target: Path, store, tmp: Path, backup: str | None = None)->str:
    target=Path(target)
    tmp=Path(tmp)
    tmp.mkdir(parents=True,exist_ok=True)
    if backup is not None and not BACKUP_RE.fullmatch(backup):
        raise SnapshotError("Backup name invalid")
    if backup is None:
        current=store.read("metadata/current.json")
        if current is None:
            if store.list_files("live"):
                raise SnapshotError("Unverified live data exists; refusing overwrite")
            target.mkdir(parents=True,exist_ok=True)
            return "new"
        try: backup=json.loads(current)["backup_name"]
        except (ValueError,KeyError,TypeError):raise SnapshotError("Invalid checkpoint metadata") from None
    if not BACKUP_RE.fullmatch(backup):
        raise SnapshotError("Invalid checkpoint backup name")
    record=store.read("backups/"+backup+".json")
    if record is None:
        raise SnapshotError("No verified manifest for selected backup")
    try:
        manifest=json.loads(record)
        if manifest["backup_name"]!=backup or not manifest.get("verified"):
            raise SnapshotError("Manifest does not match backup")
        expected=manifest["sha256"]
        size=int(manifest["size_bytes"])
        if size<0:raise ValueError()
    except (KeyError,ValueError,TypeError):
        raise SnapshotError("Invalid backup manifest") from None
    free=shutil.disk_usage(tmp).free
    if free < size + RESERVE:
        raise SnapshotError("Runner SSD too small to restore backup")
    archive=tmp/"restore.tar.gz"
    try:store.download("backups/"+backup,archive)
    except (OSError,IOError):raise SnapshotError("Failed to download backup") from None
    if archive.stat().st_size != size:
        raise SnapshotError("Backup size mismatch")
    validate_and_extract(archive,target,expected)
    return "restored"

class RcloneStore:
    def __init__(self,remote="drive:Minecraft-Hosting"):
        self.remote=remote.rstrip('/')

    def _r(self,key):
        return self.remote+'/'+key.lstrip('/')

    def _exec(self,args,input_bytes=None,timeout=180):
        try:
            p=subprocess.run(["rclone",*args],input=input_bytes,capture_output=True,timeout=timeout)
        except (OSError,subprocess.TimeoutExpired):
            raise SnapshotError("Google Drive unavailable") from None
        if p.returncode:
            raise SnapshotError("Google Drive operation failed")
        return p.stdout

    def upload(self,file,key): self._exec(["copyto",str(file),self._r(key)],timeout=600)
    def download(self,key,file): self._exec(["copyto",self._r(key),str(file)],timeout=600)
    def put(self,key,content:bytes):self._exec(["rcat",self._r(key)],input_bytes=content)
    def sync(self,source,key):self._exec(["sync",str(source),self._r(key),"--transfers","4"],timeout=600)
    def check(self,source,key):self._exec(["check",str(source),self._r(key),"--one-way"],timeout=600)
    def list_files(self,key):
        return self._exec(["lsf",self._r(key)],timeout=60).decode().splitlines()
    def read(self,key):
        try:
            return self._exec(["cat",self._r(key)],timeout=60)
        except SnapshotError:
            # Cat failed. Differentiate absent metadata from a Drive outage.
            try:
                folder,filename=key.rsplit('/',1)
                listing=self.list_files(folder)
            except (SnapshotError,ValueError):
                return None if key=="metadata/current.json" and not self.list_files("live") else None
            if filename not in listing:return None
            raise SnapshotError("Could not read known Drive object")
    def hash_remote(self,key):
        try:
            proc=subprocess.Popen(["rclone","cat",self._r(key)],
                                  stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        except OSError: raise SnapshotError("Drive read unavailable") from None
        d=hashlib.sha256()
        with proc.stdout as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):d.update(chunk)
        if proc.wait(timeout=600):
            raise SnapshotError("Remote checksum read failed")
        return d.hexdigest()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=("backup","restore","check-space"))
    parser.add_argument("--source",type=Path)
    parser.add_argument("--server",type=Path)
    parser.add_argument("--temp",type=Path,required=True)
    parser.add_argument("--drive",default="drive:Minecraft-Hosting")
    parser.add_argument("--backup",default="")
    parser.add_argument("--run-id",type=int,default=0)
    args=parser.parse_args()
    try:
        store=RcloneStore(args.drive)
        if args.action=="check-space":
            required_free_bytes(source_size(args.source),shutil.disk_usage(args.temp).free)
            print("SSD_SPACE_OK")
        elif args.action=="backup":
            result=backup_world(args.source,store,args.temp,args.run_id,snapshot_ready=True)
            print("DRIVE_BACKUP_VERIFIED="+result['backup_name'])
        elif args.action=="restore":
            result=restore_world(args.server,store,args.temp,args.backup or None)
            print("DRIVE_RESTORE="+result)
    except SnapshotError as exc:
        print("WORLD_STORE_ERROR="+str(exc),file=__import__("sys").stderr)
        raise SystemExit(1)

if __name__=="__main__":main()
