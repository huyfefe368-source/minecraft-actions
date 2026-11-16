import importlib.util,json,os,threading,unittest,tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from unittest.mock import patch
os.environ.setdefault('RUNNER_TEMP','/tmp')
class ServerControls(unittest.TestCase):
 def test_minecraft_stop_does_not_stop_job(self):
  spec=importlib.util.spec_from_file_location('runner','scripts/temp-purpur-runner.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  http=ThreadingHTTPServer(('127.0.0.1',0),m.Console)
  thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
  def post(body):
   with urlopen(Request('http://127.0.0.1:'+str(http.server_port),data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})) as r:return json.load(r)
  try:
   self.assertTrue(post({'op':'server-control','action':'stop'})['ok'])
   self.assertEqual(list(m.server_requests),['stop']);self.assertFalse(m.stop_requested.is_set())
   post({'command':'stop'});self.assertFalse(m.stop_requested.is_set())
   post({'op':'job-stop'});self.assertTrue(m.stop_requested.is_set())
  finally:http.shutdown();http.server_close();thread.join()
 def test_sync_failure_in_minecraft_action_keeps_job_alive(self):
  spec=importlib.util.spec_from_file_location('runner','scripts/temp-purpur-runner.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  with patch.object(m,'shutdown'),patch.object(m,'checkpoint',side_effect=OSError('temporary transfer failure')):
   self.assertFalse(m.handle_server_action('restart'))
   self.assertTrue(m.server_desired);self.assertTrue(m.sync_restart_pending)
   self.assertFalse(m.stop_requested.is_set())
  with patch.object(m,'prepare') as prepare,patch.object(m,'start') as start:
   m.resume_minecraft();start.assert_called_once();self.assertFalse(m.sync_restart_pending)
  with patch.object(m,'shutdown'),patch.object(m,'checkpoint',side_effect=OSError('temporary transfer failure')):
   self.assertFalse(m.handle_server_action('stop'));self.assertFalse(m.server_desired)
   self.assertFalse(m.stop_requested.is_set())

 def test_prepare_preserves_synced_properties(self):
  spec=importlib.util.spec_from_file_location('runner','scripts/temp-purpur-runner.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  with tempfile.TemporaryDirectory() as d:
   m.SERVER=Path(d)
   (m.SERVER/'server.jar').write_bytes(b'existing jar')
   (m.SERVER/'eula.txt').write_text('eula=true')
   original='server-port=25580\nonline-mode=false\nmotd=Edited in repo\n'
   (m.SERVER/'server.properties').write_text(original)
   with patch.object(m,'run'):m.prepare()
   self.assertEqual((m.SERVER/'server.properties').read_text(),original)
   self.assertEqual(m.minecraft_port(),25580)

if __name__=='__main__':unittest.main()
