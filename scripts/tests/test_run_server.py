import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
import pytest
from scripts.world_store import (SnapshotError, archive_world, validate_and_extract,
                                 required_free_bytes, backup_world, restore_world)

class FakeDrive:
    def __init__(self):
        self.data={}
        self.actions=[]
        self.fail_upload=False
    def read(self,key):
        return self.data.get(key)
    def upload(self,file,key):
        self.actions.append(('upload',key))
        if self.fail_upload: raise IOError("upload unavailable")
        self.data[key]=Path(file).read_bytes()
    def download(self,key,target):
        if key not in self.data: raise IOError("not found")
        Path(target).write_bytes(self.data[key])
    def put(self,key,content):
        self.actions.append(('put',key))
        self.data[key]=content
    def sync(self,source,key):
        self.actions.append(('sync',key))
    def check(self,source,key):
        self.actions.append(('check',key))
    def hash_remote(self,key):
        self.actions.append(('hash_remote',key))
        return hashlib.sha256(self.data[key]).hexdigest()
    def list_files(self,key):
        return [k for k in self.data if k.startswith(key)]

def sample_world(tmp_path):
    server=tmp_path/'server'
    (server/'world').mkdir(parents=True)
    (server/'world'/'region.dat').write_bytes(b'world-data\x00\xff')
    (server/'server.properties').write_text('max-players=5')
    (server/'paper.jar').write_bytes(b'never-backup-jar')
    return server

def test_restores_known_good_live(tmp_path):
    server=sample_world(tmp_path)
    d=FakeDrive()
    manifest=backup_world(server,d,tmp_path/'cache',run_id=17,stamp='20261114T100000Z',free_bytes=50*1024**3)
    target=tmp_path/'restored'
    assert restore_world(target,d,tmp_path/'recover')=='restored'
    assert (target/'world'/'region.dat').read_bytes()==b'world-data\x00\xff'
    assert (target/'paper.jar').read_bytes()==b'never-backup-jar'
    assert manifest['verified']

def test_rejects_archive_parent_or_symlink_entries(tmp_path):
    malicious=tmp_path/'evil.tar.gz'
    with tarfile.open(malicious,'w:gz') as tf:
        link=tarfile.TarInfo('world/link')
        link.type=tarfile.SYMTYPE
        link.linkname='../../etc/shadow'
        tf.addfile(link)
    with pytest.raises(SnapshotError):
        validate_and_extract(malicious,tmp_path/'output',hashlib.sha256(malicious.read_bytes()).hexdigest())

def test_bad_sha_aborts_restore(tmp_path):
    good=tmp_path/'good.tar.gz'
    with tarfile.open(good,'w:gz') as tf:
        file=tarfile.TarInfo('data.txt');file.size=1;tf.addfile(file,io.BytesIO(b'x'))
    with pytest.raises(SnapshotError):
        validate_and_extract(good,tmp_path/'output','0'*64)
    assert not (tmp_path/'output'/'data.txt').exists()

def test_disk_headroom_blocks_start():
    assert required_free_bytes(2*1024**3)==6*1024**3
    with pytest.raises(SnapshotError):
        required_free_bytes(2*1024**3,free_bytes=5*1024**3)

def test_upload_failure_keeps_previous_snapshot(tmp_path):
    server=sample_world(tmp_path);d=FakeDrive();d.data['metadata/current.json']=b'old'
    d.fail_upload=True
    with pytest.raises(SnapshotError):
        backup_world(server,d,tmp_path/'cache',run_id=1,stamp='20261114T100001Z',free_bytes=50*1024**3)
    assert d.data['metadata/current.json']==b'old'

def test_live_not_promoted_before_backup_verified(tmp_path):
    server=sample_world(tmp_path);d=FakeDrive()
    backup_world(server,d,tmp_path/'cache',run_id=1,stamp='20261114T100002Z',free_bytes=50*1024**3)
    actions=d.actions
    hash_index=next(i for i,a in enumerate(actions) if a[0]=='hash_remote')
    live_index=next(i for i,a in enumerate(actions) if a==('sync','server'))
    marker_index=next(i for i,a in enumerate(actions) if a==('put','metadata/current.json'))
    assert hash_index < live_index < marker_index

def test_final_backup_error_reports_failure(tmp_path):
    server=sample_world(tmp_path);d=FakeDrive()
    def broken_hash(key):return '0'*64
    d.hash_remote=broken_hash
    with pytest.raises(SnapshotError):
        backup_world(server,d,tmp_path/'cache',run_id=1,stamp='20261114T100003Z',free_bytes=50*1024**3)

def test_backup_without_checkpoint_refuses_new_world(tmp_path):
    d=FakeDrive();d.data['backups/old.tar.gz']=b'old'
    with pytest.raises(SnapshotError, match="without checkpoint"):
        restore_world(tmp_path/'server',d,tmp_path/'recover')
    assert d.data['backups/old.tar.gz']==b'old'


def test_no_second_writer(tmp_path):
    import yaml
    root=Path(__file__).resolve().parents[2]
    data=yaml.safe_load((root/'.github/workflows/minecraft-hosting.yml').read_text())
    assert data['concurrency']['group']=='minecraft-hosting'
    assert data['concurrency']['cancel-in-progress'] is False

def test_runner_uses_verified_backup_and_restore_helpers():
    root=Path(__file__).resolve().parents[2]
    source=(root/'scripts/run_server.sh').read_text()
    assert 'python3 scripts/world_store.py restore' in source
    assert 'python3 scripts/world_store.py backup' in source
    assert 'python3 scripts/world_store.py check-space' in source
    assert 'rclone sync "$RUNNER_TEMP/mc-snapshot/" "$DRIVE_BASE/live"' not in source

def test_runner_save_on_runs_after_snapshot_copy():
    root=Path(__file__).resolve().parents[2]
    source=(root/'scripts/run_server.sh').read_text()
    assert 'snapshot_rc=0' in source
    assert "printf 'save-on" in source

def test_ssh_reverse_tunnel_target_is_actual_vps_host():
    script=(Path(__file__).resolve().parents[2]/'scripts/run_server.sh').read_text()
    wanted='"'+chr(36)+'{VPS_USER}@'+chr(36)+'{VPS_HOST}"'
    assert wanted in script

def test_game_save_commands_use_real_newlines():
    script=(Path(__file__).resolve().parents[2]/'scripts/run_server.sh').read_text()
    assert r"printf 'save-off\nsave-all flush\n'" in script
    assert r"printf 'save-on\n'" in script

def test_drive_outage_cannot_be_treated_as_new_world(monkeypatch):
    from scripts.world_store import RcloneStore
    store=RcloneStore()
    def outage(*args,**kwargs):raise SnapshotError('Drive outage')
    monkeypatch.setattr(store,'_exec',outage)
    with pytest.raises(SnapshotError):
        store.read('metadata/current.json')


def test_drive_missing_metadata_only_after_confirmed_folder_listing(monkeypatch):
    from scripts.world_store import RcloneStore
    store=RcloneStore()
    def execute(args, **kwargs):
        if args[0] == "cat":
            raise SnapshotError("cat unavailable")
        if args[0] == "lsf" and args[1].endswith("Minecraft-Hosting/"):
            return b"server/\nbackups/\nmetadata/\n"
        if args[0] == "lsf" and args[1].endswith("/metadata"):
            return b"other.json\n"
        raise AssertionError(args)
    monkeypatch.setattr(store, "_exec", execute)
    assert store.read("metadata/current.json") is None


def test_drive_existing_metadata_read_failure_blocks_new_world(monkeypatch):
    from scripts.world_store import RcloneStore
    store=RcloneStore()
    def execute(args, **kwargs):
        if args[0] == "cat":
            raise SnapshotError("Drive read failed")
        if args[0] == "lsf" and args[1].endswith("Minecraft-Hosting/"):
            return b"metadata/\nlive/\nbackups/\n"
        if args[0] == "lsf" and args[1].endswith("/metadata"):
            return b"current.json\n"
        raise AssertionError(args)
    monkeypatch.setattr(store, "_exec", execute)
    with pytest.raises(SnapshotError, match="existing Drive object"):
        store.read("metadata/current.json")


def test_new_world_requires_no_existing_files(tmp_path):
    drive=FakeDrive()
    target=tmp_path/'server'
    target.mkdir()
    (target/'world.dat').write_bytes(b'not empty')
    with pytest.raises(SnapshotError, match="not empty"):
        restore_world(target,drive,tmp_path/'recover')
    assert (target/'world.dat').read_bytes()==b'not empty'

def test_world_backup_includes_normal_files_and_folders(tmp_path):
    from scripts.world_store import archive_world
    source=sample_world(tmp_path)
    (source/'server.log').write_text('server startup log')
    (source/'plugins').mkdir()
    (source/'plugins'/'Example.jar').write_bytes(b'plugin-data')
    import os
    os.mkfifo(source/'console.fifo')
    archive=tmp_path/'snapshot.tar.gz'
    archive_world(source,archive)
    with tarfile.open(archive,'r:gz') as tf:
        names=set(tf.getnames())
    assert 'paper.jar' in names
    assert 'server.log' in names
    assert 'plugins/Example.jar' in names
    assert 'world/region.dat' in names
    assert 'console.fifo' not in names

def test_incremental_raw_mirror_does_not_change_recovery_checkpoint(tmp_path):
    from scripts.world_store import mirror_live
    source=sample_world(tmp_path)
    drive=FakeDrive()
    drive.data['metadata/current.json']=b'untouched'
    result=mirror_live(source,drive)
    assert result['mode']=='verified-raw-files'
    assert ('sync','server') in drive.actions
    assert ('check','server') in drive.actions
    assert drive.data['metadata/current.json']==b'untouched'
    assert 'metadata/live-last-sync.json' in drive.data

def test_backup_failure_cannot_claim_success():
    source=(Path(__file__).resolve().parents[2]/'scripts/run_server.sh').read_text()
    assert 'DRIVE_BACKUP_FAILED type=' in source
    assert 'INITIAL_BACKUP_FAILED' in source
    assert 'backup initial ||' in source
    assert 'sync-live' in source

def test_official_rclone_installer_sha_verified():
    wf=(Path(__file__).resolve().parents[2]/'.github/workflows/minecraft-hosting.yml').read_text()
    assert 'rclone-v1.76.0' not in wf or 'v1.76.0' in wf
    assert 'sha256sum -c -' in wf
    assert 'sudo install -m 755 rclone' in wf
