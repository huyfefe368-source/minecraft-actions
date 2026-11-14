"""Execute the actual shell backup function with controlled failures."""
from pathlib import Path
import subprocess

SOURCE=(Path(__file__).resolve().parents[2]/"scripts/run_server.sh").read_text()

def backup_function():
    start=SOURCE.index("\nbackup() {")+1
    end=SOURCE.index("\ncleanup() {",start)
    return SOURCE[start:end]

def test_backup_helper_propagates_failed_drive_upload():
    script="\n".join([
        "set -euo pipefail",
        'RUNNER_TEMP=/tmp',
        'DRIVE_BASE=drive:test',
        'GITHUB_RUN_ID=123',
        'snapshot_server(){ return 0; }',
        'python3(){ return 1; }',
        backup_function(),
        'backup final',
    ])
    result=subprocess.run(["bash","-c",script],capture_output=True,text=True)
    assert result.returncode != 0
    assert "DRIVE_BACKUP_FAILED type=final" in result.stdout
    assert "DRIVE_BACKUP_OK" not in result.stdout

def test_backup_helper_announces_success_only_on_true_success():
    script="\n".join([
        "set -euo pipefail",
        'RUNNER_TEMP=/tmp',
        'DRIVE_BASE=drive:test',
        'GITHUB_RUN_ID=123',
        'snapshot_server(){ return 0; }',
        'python3(){ return 0; }',
        backup_function(),
        'backup initial',
    ])
    result=subprocess.run(["bash","-c",script],capture_output=True,text=True)
    assert result.returncode==0
    assert "DRIVE_BACKUP_OK type=initial" in result.stdout
