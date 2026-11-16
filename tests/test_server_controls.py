import importlib.util,json,os,threading,unittest,tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
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
if __name__=='__main__':unittest.main()
