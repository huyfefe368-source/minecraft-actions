import importlib.util
import json
import tempfile
import unittest
import subprocess
import sys
import threading
import time
from unittest.mock import patch
from pathlib import Path

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class PersistenceTests(unittest.TestCase):
    def test_readiness_requires_done_from_current_boot(self):
        import os
        os.environ.setdefault('RUNNER_TEMP','/tmp')
        m=load('runner','scripts/temp-purpur-runner.py')
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'console.log'
            log.write_text('[INFO]: Done (15.0s)! For help, type help\n')
            offset=log.stat().st_size
            with log.open('a') as f:f.write('Starting Minecraft server on 127.0.0.1:25565\n')
            self.assertFalse(m.startup_ready(log,offset))
            with log.open('a') as f:f.write('[INFO]: Done (16.071s)! For help, type help\n')
            self.assertTrue(m.startup_ready(log,offset))

    def test_manifest_detects_partial_transfer_and_extra_files(self):
        m = load('state', 'scripts/temp_mc_state.py')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'world').mkdir()
            (root/'world/level.dat').write_bytes(b'world')
            manifest=m.inventory(root)
            m.verify(root,manifest)
            (root/'world/level.dat').write_bytes(b'partial')
            with self.assertRaises(ValueError):m.verify(root,manifest)
            (root/'world/level.dat').write_bytes(b'world')
            (root/'extra').write_text('stale')
            with self.assertRaises(ValueError):m.verify(root,manifest)

    def test_symlink_cannot_publish_files_outside_server(self):
        m=load('state','scripts/temp_mc_state.py')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'escape').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):m.inventory(root)

    def test_cleanup_signal_does_not_interrupt_final_backup(self):
        import os
        os.environ.setdefault('RUNNER_TEMP','/tmp')
        m=load('runner','scripts/temp-purpur-runner.py')
        m.on_signal(2,None)
        self.assertTrue(m.stop_requested.is_set())

    def test_stop_interrupts_inflight_checkpoint_command(self):
        import os
        os.environ.setdefault('RUNNER_TEMP','/tmp')
        m=load('runner','scripts/temp-purpur-runner.py')
        with tempfile.TemporaryDirectory() as d:
            m.BASE=Path(d);m.phase='checkpoint'
            timer=threading.Timer(0.2,lambda:(m.BASE/'stop-requested').touch())
            timer.start();started=time.monotonic()
            try:
                with self.assertRaises(m.CheckpointInterrupted):
                    m.run([sys.executable,'-c','import time;time.sleep(10)'])
            finally:timer.join()
            self.assertLess(time.monotonic()-started,3)

    def test_final_transfer_obeys_overall_cleanup_deadline(self):
        import os
        os.environ.setdefault('RUNNER_TEMP','/tmp')
        m=load('runner','scripts/temp-purpur-runner.py')
        m.phase='saving';m.cleanup_deadline=time.monotonic()+0.2
        started=time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            m.run([sys.executable,'-c','import time;time.sleep(10)'])
        self.assertLess(time.monotonic()-started,3)
        m.on_signal(15,None)
        self.assertTrue(m.stop_requested.is_set())

    def test_cancel_during_restore_never_publishes_partial_tree(self):
        import os,signal
        os.environ.setdefault('RUNNER_TEMP','/tmp')
        m=load('runner','scripts/temp-purpur-runner.py')
        with tempfile.TemporaryDirectory() as d:
            m.BASE=Path(d)
            timer=threading.Timer(0.2,lambda:(m.BASE/'stop-requested').touch())
            def restore():m.run([sys.executable,'-c','import time;time.sleep(10)'])
            original={sig:signal.getsignal(sig) for sig in (signal.SIGINT,signal.SIGTERM)}
            timer.start();started=time.monotonic()
            try:
                with patch.object(sys,'argv',['runner.py','20']),patch.object(m,'setup'),patch.object(m,'restore',restore),patch.object(m,'checkpoint',side_effect=AssertionError('Partial tree published')):
                    self.assertEqual(m.main(),0)
            finally:
                timer.join()
                for sig,handler in original.items():signal.signal(sig,handler)
            self.assertLess(time.monotonic()-started,3)

    def test_failed_git_push_retains_snapshot_and_retry_publishes(self):
        sys.path.insert(0,'scripts')
        m=load('publisher','scripts/temp_mc_publish.py')
        state=load('state','scripts/temp_mc_state.py')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); remote=root/'remote.git'; repo=root/'repo'
            def git(*args):
                return subprocess.run(['git',*map(str,args)],check=True,capture_output=True)
            git('init','--bare',remote)
            git('clone',remote,repo)
            git('-C',repo,'config','user.email','test@example.test')
            git('-C',repo,'config','user.name','Test')
            git('-C',repo,'checkout','-b','main')
            (repo/'README').write_text('test')
            git('-C',repo,'add','.');git('-C',repo,'commit','-m','init')
            git('-C',repo,'push','-u','origin','main')
            hook=remote/'hooks/pre-receive';hook.write_text('#!/bin/sh\nexit 1\n');hook.chmod(0o755)
            incoming=root/'tree-incoming/123-1'; server=incoming/'server'
            (server/'world').mkdir(parents=True)
            (server/'world/level.dat').write_bytes(b'world')
            (server/'server.jar').write_bytes(b'jar')
            (incoming/'manifest.json').write_text(json.dumps({'id':'123-1','final':True,'files':state.inventory(server)}))
            (incoming/'READY').touch()
            rejected=root/'tree-incoming/122-0'
            (rejected/'server').mkdir(parents=True)
            (rejected/'manifest.json').write_text('{broken json')
            (rejected/'READY').touch()
            m.BASE=root;m.REPO=repo
            with self.assertRaises(subprocess.CalledProcessError):m.publish(root/'lock')
            self.assertTrue((root/'tree-rejected/122-0/READY').exists())
            self.assertTrue((incoming/'READY').exists())
            self.assertFalse((root/'published.json').exists())
            hook.unlink();m.publish(root/'lock')
            self.assertFalse(incoming.exists())
            self.assertTrue(json.loads((root/'published.json').read_text())['final'])
            self.assertEqual((repo/'server/world/level.dat').read_bytes(),b'world')

if __name__=='__main__':unittest.main()
