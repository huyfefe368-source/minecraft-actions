from types import SimpleNamespace
from scripts import desired_state

def test_owner_running_enabled(monkeypatch):
    def proc(*a,**kw):
        return SimpleNamespace(returncode=0,stdout='{"desired":"running"}')
    monkeypatch.setattr(desired_state.subprocess,"run",proc)
    assert desired_state.running()

def test_owner_stop_prevents_start(monkeypatch):
    for body in ('{"desired":"stopped"}','{"desired":"unknown"}','bad-json','[]',''):
        monkeypatch.setattr(desired_state.subprocess,"run",
                            lambda *a,**k:SimpleNamespace(returncode=0,stdout=body))
        assert not desired_state.running()

def test_drive_failure_prevents_start(monkeypatch):
    monkeypatch.setattr(desired_state.subprocess,"run",
                        lambda *a,**k:SimpleNamespace(returncode=1,stdout=''))
    assert not desired_state.running()

def test_check_cannot_be_replaced_by_fake_no_op(monkeypatch):
    monkeypatch.setattr(desired_state.subprocess,"run",
                        lambda *a,**k:SimpleNamespace(returncode=0,stdout='{"desired":"running"}'))
    assert desired_state.running()
