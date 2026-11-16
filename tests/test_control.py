import sys,time,unittest
sys.path.insert(0,'scripts')
from temp_mc_control import can_restart
class ControlTests(unittest.TestCase):
 def test_restart_requires_final_snapshot(self):
  s={'enabled':True,'last_run':123}
  self.assertFalse(can_restart(s,[],{'snapshot':'123-1','final':False}))
  self.assertFalse(can_restart(s,[],{'snapshot':'122-1','final':True}))
  self.assertTrue(can_restart(s,[],{'snapshot':'123-1','final':True}))
 def test_disabled_active_and_recent_dispatch_never_duplicate(self):
  self.assertFalse(can_restart({'enabled':False},[],{}))
  self.assertFalse(can_restart({'enabled':True},[{'status':'queued'}],{}))
  self.assertFalse(can_restart({'enabled':True,'dispatched_at':time.time()},[],{}))
if __name__=='__main__':unittest.main()
