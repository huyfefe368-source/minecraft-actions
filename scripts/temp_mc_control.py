"""Fixed-operation root controller; never accepts shell commands or credentials."""
import fcntl,json,os,re,subprocess,sys,time
from pathlib import Path
from urllib.request import Request,urlopen
BASE=Path('/srv/mc-temp12111')
REPO='huyfefe368-source/minecraft-actions'
WORKFLOW='temp-purpur-playtest.yml'

def gh(*args):
 return subprocess.check_output(['gh',*args],text=True,timeout=25,env={**os.environ,'HOME':'/root','GH_PROMPT_DISABLED':'1'})

def can_restart(state,runs,published):
 if not state.get('enabled') or any(r['status']!='completed' for r in runs):return False
 if time.time()-state.get('dispatched_at',0)<90:return False
 last=state.get('last_run')
 if last and not (published.get('final') and published.get('snapshot','').startswith(str(last)+'-')):return False
 return True

def main(action):
 if action not in ('status','start','stop','enable','disable','tick'):raise ValueError('Invalid action')
 with open('/run/lock/mc-controller.lock','w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  path=BASE/'controller.json'
  state=json.loads(path.read_text()) if path.exists() else {'enabled':False}
  runs=json.loads(gh('run','list','-R',REPO,'--workflow',WORKFLOW,'--limit','8','--json','databaseId,status,conclusion,url'))
  active=[r for r in runs if r['status']!='completed']
  saved=BASE/'published.json';published=json.loads(saved.read_text()) if saved.exists() else {}
  def dispatch():
   output=gh('workflow','run',WORKFLOW,'-R',REPO,'--ref','main','-f','duration_minutes=330')
   match=re.search(r'/runs/(\d+)',output)
   state['dispatched_at']=time.time()
   if match:state['last_run']=int(match.group(1))
   state['message']='Đã yêu cầu bật job; đang chờ GitHub cấp máy'
  if action=='stop':
   state['enabled']=False;state['message']='Đang dừng và lưu; 24/7 đã tắt'
   try:
    with urlopen(Request('http://127.0.0.1:25579/',data=b'{"command":"stop"}',headers={'Content-Type':'application/json'}),timeout=4) as r:r.read()
   except OSError:
    for run in active:gh('run','cancel',str(run['databaseId']),'-R',REPO)
  elif action=='disable':state['enabled']=False;state['message']='24/7 đã tắt; phiên hiện tại tiếp tục chạy'
  elif action in ('start','enable'):
   if action=='enable':state['enabled']=True
   if not active and time.time()-state.get('dispatched_at',0)>=90:dispatch()
   else:state['message']='Job đang chạy hoặc đang khởi tạo'
  elif action=='tick' and can_restart(state,runs,published):dispatch()
  if active:state['last_run']=active[0]['databaseId']
  state['active']=active;state['updated_at']=time.time()
  temp=path.with_suffix('.tmp');temp.write_text(json.dumps(state));temp.chmod(0o644);temp.replace(path)
  return state

if __name__=='__main__':
 try:print(json.dumps(main(sys.argv[1])))
 except Exception as error:print(json.dumps({'error':str(error)}));sys.exit(1)
