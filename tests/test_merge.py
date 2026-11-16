import shutil
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from temp_mc_state import inventory
try:
    from temp_mc_merge import merge_trees
except ImportError:
    merge_trees = None

class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.local=self.root/'local'; self.remote=self.root/'remote'
        self.local.mkdir(); self.remote.mkdir()
        self.target=self.root/'target'; self.conflicts=self.root/'conflicts'
    def put(self,root,path,text):
        p=root/path; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text)
    def baseline(self,files):
        for path,text in files.items():
            self.put(self.local,path,text); self.put(self.remote,path,text)
        return inventory(self.local)
    def merge(self,base):
        self.assertIsNotNone(merge_trees,'merge implementation missing')
        return merge_trees(base,self.local,self.remote,self.target,self.conflicts)
    def test_three_way_add_delete_and_convergence(self):
        base=self.baseline({'local':'old','remote':'old','gone':'old','same':'old'})
        self.put(self.local,'local','repo'); self.put(self.remote,'remote','runtime')
        (self.remote/'gone').unlink()
        for root in (self.local,self.remote): self.put(root,'same','new')
        self.put(self.local,'added-local','a'); self.put(self.remote,'added-remote','b')
        result=self.merge(base)
        self.assertEqual(result['conflicts'],[])
        self.assertEqual((self.target/'local').read_text(),'repo')
        self.assertEqual((self.target/'remote').read_text(),'runtime')
        self.assertFalse((self.target/'gone').exists())
        self.assertEqual(result['applied_remote'],['added-remote','gone','remote'])
        self.assertEqual(result['files'],inventory(self.target))
    def test_conflicts_keep_both_and_logs_choose_local(self):
        base=self.baseline({'config':'old','logs/latest.log':'old','deleted':'old'})
        for path in base: self.put(self.local,path,'repo'); self.put(self.remote,path,'runtime')
        (self.remote/'deleted').unlink()
        result=self.merge(base)
        self.assertEqual(result['conflicts'],['config','deleted','logs/latest.log'])
        self.assertEqual((self.target/'config').read_text(),'runtime')
        self.assertEqual((self.target/'logs/latest.log').read_text(),'repo')
        self.assertEqual((self.conflicts/'local/config').read_text(),'repo')
        self.assertEqual((self.conflicts/'remote/config').read_text(),'runtime')
        self.assertIn('deleted',(self.conflicts/'deletions.json').read_text())
    def test_world_is_selected_as_whole_remote_version(self):
        base=self.baseline({'world/region/a.mca':'old','world/level.dat':'old'})
        self.put(self.local,'world/region/a.mca','repo')
        self.put(self.remote,'world/level.dat','runtime')
        result=self.merge(base)
        self.assertEqual((self.target/'world/region/a.mca').read_text(),'old')
        self.assertEqual((self.conflicts/'local/world/region/a.mca').read_text(),'repo')
        self.assertEqual(result['applied_remote'],['world/level.dat','world/region/a.mca'])
        self.assertIn('world',result['conflicts'])
    def test_whole_world_deletion(self):
        base=self.baseline({'world/level.dat':'old'})
        (self.remote/'world/level.dat').unlink()
        self.merge(base)
        self.assertFalse((self.target/'world').exists())
    def test_unsafe_baseline_and_symlinks_leave_target_absent(self):
        for key in ('../escape','/absolute','.git/config','a/../b'):
            with self.subTest(key=key):
                with self.assertRaises(ValueError): self.merge({key:{'size':0,'sha256':'x'}})
                self.assertFalse(self.target.exists())
        (self.local/'link').symlink_to(self.remote)
        with self.assertRaises(ValueError): self.merge({})
        self.assertFalse(self.target.exists())
    def test_empty_git_directory_is_rejected(self):
        (self.local/'.git').mkdir()
        with self.assertRaises(ValueError): self.merge({})
        self.assertFalse(self.target.exists())
    def test_same_size_corruption_is_detected_as_remote_change(self):
        base=self.baseline({'level':'good'})
        self.put(self.remote,'level','evil')
        result=self.merge(base)
        self.assertEqual((self.target/'level').read_text(),'evil')
        self.assertEqual(result['applied_remote'],['level'])
    def test_file_directory_replacement(self):
        base=self.baseline({'thing':'old'})
        (self.remote/'thing').unlink(); self.put(self.remote,'thing/child','new')
        self.merge(base)
        self.assertEqual((self.target/'thing/child').read_text(),'new')
    def test_incompatible_independent_changes_reject_before_copy(self):
        self.put(self.local,'thing','repo'); self.put(self.remote,'thing/child','runtime')
        with self.assertRaises(ValueError): self.merge({})
        self.assertFalse(self.target.exists())

class MetadataTests(unittest.TestCase):
    def test_unchanged_file_mtime_survives_merge(self):
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            local=root/'local';remote=root/'remote';local.mkdir();remote.mkdir()
            (local/'server.jar').write_bytes(b'jar')
            os.utime(local/'server.jar',ns=(100000000000,100000000000))
            shutil.copy2(local/'server.jar',remote/'server.jar')
            base=inventory(local)
            merge_trees(base,local,remote,root/'target',root/'conflict')
            self.assertEqual((root/'target/server.jar').stat().st_mtime_ns,(local/'server.jar').stat().st_mtime_ns)

if __name__=='__main__': unittest.main()
