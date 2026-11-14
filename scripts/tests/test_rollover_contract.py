from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]

def test_workflow_can_dispatch_verified_followup():
    data=yaml.safe_load((ROOT/'.github/workflows/minecraft-hosting.yml').read_text())
    assert data["permissions"]["actions"]=="write"
    assert data["concurrency"]["cancel-in-progress"] is False
    text=(ROOT/'.github/workflows/minecraft-hosting.yml').read_text()
    assert "github.token" in text
    assert "feat/minecraft-hosting-v2" in text

def test_manual_stop_fences_successor():
    script=(ROOT/'scripts/run_server.sh').read_text()
    assert script.index("dispatch_next()") < script.index("backup()")
    assert script.count('scripts/desired_state.py check') >= 5
    assert 'mc-stop-requested' in script
    assert 'mc-restart-requested' in script
    assert 'dispatch_next || rc=1' in script
    assert script.index('backup final') < script.index('dispatch_next || rc=1')
    assert 'ROLLOVER_READY=true' in script
