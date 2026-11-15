import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest
from scripts import startup_config,world_store

ROOT=Path(__file__).resolve().parents[2]

def test_all_runtime_versions_from_panel():
    for version in ("8","11","17","21","25"):
        obj=startup_config.validate({
            "java_version":version,"command":"java -jar server.jar nogui","software":"Custom"})
        assert obj["java_version"]==version

def test_sh_launcher_is_executable_without_java_or_shell_eval(tmp_path):
    server=tmp_path/"server"
    server.mkdir()
    (server/"run.sh").write_text("#!/bin/sh\nprintf 'server launched with %s\\n' \"$*\"\n")
    config=tmp_path/"startup.json"
    config.write_text(json.dumps({"java_version":"17","command":"sh run.sh nogui","software":"Forge"}))
    fake_java=tmp_path/"jdk17"/"bin"/"java"
    fake_java.parent.mkdir(parents=True)
    fake_java.write_text("#!/bin/sh\nexit 0\n")
    fake_java.chmod(0o755)
    env={**os.environ,"JAVA_HOME_17_X64":str(fake_java.parent.parent)}
    proc=subprocess.run([sys.executable,str(ROOT/"scripts/startup.py"),
                         "launch","--config",str(config),"--root",str(server)],
                        capture_output=True,text=True,timeout=8,env=env)
    assert proc.returncode==0,proc.stderr
    assert "server launched with nogui" in proc.stdout

def test_missing_selected_jar_fails_before_launch(tmp_path):
    server=tmp_path/"server";server.mkdir()
    cfg=tmp_path/"startup.json"
    cfg.write_text(json.dumps({"java_version":"25","command":"java -jar server.jar nogui","software":"Custom"}))
    p=subprocess.run([sys.executable,str(ROOT/"scripts/startup.py"),"launch",
                      "--config",str(cfg),"--root",str(server)],
                     capture_output=True,text=True,timeout=8)
    assert p.returncode==1
    assert "STARTUP_FILE_MISSING" in p.stderr

def test_runner_is_generic_without_fixed_memory_or_version():
    shell=(ROOT/"scripts/run_server.sh").read_text()
    workflow=(ROOT/".github/workflows/minecraft-hosting.yml").read_text()
    assert "-Xmx12G" not in shell and "-Xms2G" not in shell
    assert "PAPER_JAR_DOWNLOADED" not in shell
    assert "fill.papermc.io" not in shell
    assert "scripts/startup.py prepare" in shell
    assert "scripts/startup.py launch" in shell
    assert "world_store.py restore-live" in shell
    for version in ("8","11","17","21","25"):
        assert "\n            "+version+"\n" in workflow or workflow.rstrip().endswith("\n            "+version)

class DriveLive:
    def __init__(self,state=None):
        self.state=state
        self.downloads=0
    def list_files(self,key):
        return ["server.jar","plugins/"] if key=="server" else []
    def read(self,key):
        return self.state if key=="metadata/live-state.json" else None
    def download_live(self,target):
        self.downloads+=1
        (target/"plugins").mkdir(parents=True)
        (target/"server.jar").write_bytes(b"server-jar")
        (target/"plugins"/"config.yml").write_bytes(b"configured")
    def check_live(self,target):
        assert (target/"server.jar").read_bytes()==b"server-jar"

def test_offline_drive_files_are_authoritative_for_next_start(tmp_path):
    store=DriveLive()
    server=tmp_path/"server"
    assert world_store.restore_server(server,store,tmp_path/"cache")=="server"
    assert store.downloads==1
    assert (server/"plugins"/"config.yml").read_bytes()==b"configured"

def test_good_live_marker_is_accepted(tmp_path):
    store=DriveLive(b'{"state":"ready"}')
    assert world_store.restore_server(tmp_path/"server",store,tmp_path/"cache")=="server"

def test_partial_drive_mirror_does_not_boot_world(tmp_path,monkeypatch):
    store=DriveLive(b'{"state":"updating"}')
    def fallback(*args,**kwargs):
        raise world_store.SnapshotError("No safe verified archive")
    monkeypatch.setattr(world_store,"restore_world",fallback)
    with pytest.raises(world_store.SnapshotError):
        world_store.restore_server(tmp_path/"server",store,tmp_path/"cache")
    assert store.downloads==0

def test_invalid_marker_fails_closed(tmp_path):
    with pytest.raises(world_store.SnapshotError,match="Malformed"):
        world_store.restore_server(tmp_path/"server",DriveLive(b"not-json"),tmp_path/"cache")
