"""Validate, three-way merge and publish full server trees without losing repo edits."""
import fcntl,json,shutil,subprocess,time
from pathlib import Path
from temp_mc_state import verify,inventory
from temp_mc_merge import merge_trees
BASE=Path('/srv/mc-temp12111')
REPO=BASE/'tree-repo'

def run(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def git(*args,capture=False):
 r=subprocess.run(['git','-C',str(REPO),*args],check=True,text=True,capture_output=capture)
 return r.stdout.strip() if capture else ''
def atomic_json(path,data):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data));tmp.chmod(0o644);tmp.replace(path)

def publish(lock_path='/run/lock/mc-tree-publish.lock'):
 with open(lock_path,'w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  for marker in sorted((BASE/'tree-incoming').glob('*/READY')):
   incoming=marker.parent
   try:
    info=json.loads((incoming/'manifest.json').read_text())
    verify(incoming/'server',info['files'])
    if 'world/level.dat' not in info['files'] or 'server.jar' not in info['files']:raise ValueError('Incomplete server tree')
    if any(v['size']>=100*1024*1024 for v in info['files'].values()):raise ValueError('GitHub individual file limit: 100 MiB')
   except (ValueError,OSError,KeyError,TypeError) as error:
    rejected=BASE/'tree-rejected';rejected.mkdir(exist_ok=True)
    target=rejected/incoming.name;incoming.rename(target)
    (target/'rejection.txt').write_text(str(error))
    atomic_json(BASE/'tree-acks'/(incoming.name+'.json'),{'snapshot':incoming.name,'error':str(error)})
    print('SNAPSHOT_QUARANTINED',incoming.name,str(error),flush=True);continue
   existing_ack=BASE/'tree-acks'/(incoming.name+'.json')
   if existing_ack.exists():
    shutil.rmtree(incoming);continue
   # The durable incoming tree is retained until push succeeds. HEAD never moves
   # for a rejected push, so retry can safely rebuild against the newest remote.
   git('fetch','origin','main')
   git('reset','--hard','origin/main')
   work=BASE/'tree-work'/incoming.name
   if work.exists():shutil.rmtree(work)
   work.mkdir(parents=True)
   conflict_dir=work/'conflicts'
   if 'base' in info:
    baseline=info['base']
    for previous in info.get('pending',[]):
     if not previous.startswith(info['id'].split('-')[0]+'-') or '/' in previous:raise ValueError('Invalid pending snapshot')
     ack=BASE/'tree-acks'/(previous+'.json')
     if ack.exists():baseline=json.loads(ack.read_text()).get('source_files',baseline)
    try:result=merge_trees(baseline,incoming/'server',REPO/'server',work/'server',conflict_dir)
    except ValueError as error:
     rejected=BASE/'tree-rejected';rejected.mkdir(exist_ok=True)
     incoming.rename(rejected/incoming.name)
     atomic_json(BASE/'tree-acks'/(incoming.name+'.json'),{'snapshot':incoming.name,'error':str(error)})
     print('MERGE_REJECTED',incoming.name,str(error),flush=True)
     continue
   else:
    shutil.copytree(incoming/'server',work/'server')
    result={'files':inventory(work/'server'),'applied_remote':[],'conflicts':[]}
   if any(v['size']>=100*1024*1024 for v in result['files'].values()):raise ValueError('Merged tree contains a file >=100 MiB')
   if result['conflicts']:
    shutil.copytree(conflict_dir,REPO/'conflicts'/incoming.name,dirs_exist_ok=True)
   run(['rsync','-a','--checksum','--delete',str(work/'server')+'/',str(REPO/'server')+'/'])
   # Files arrive with Minecraft's private modes; the SSH reader needs group access.
   import grp,os
   try:gid=grp.getgrnam('mcghplaytest').gr_gid
   except KeyError:gid=None
   if gid is not None:
    for entry in [REPO/'server',*(REPO/'server').rglob('*')]:
     os.chown(entry,-1,gid);os.chmod(entry,entry.stat().st_mode | (0o050 if entry.is_dir() else 0o040))
   verify(REPO/'server',result['files'])
   merged={**info,'files':result['files'],'conflicts':result['conflicts']}
   (REPO/'snapshot.json').write_text(json.dumps(merged,indent=2))
   git('add','-f','--all','server','snapshot.json')
   if (REPO/'conflicts').exists():git('add','-f','--all','conflicts')
   tree=git('write-tree',capture=True);parent=git('rev-parse','HEAD',capture=True)
   commit=git('commit-tree',tree,'-p',parent,'-m','Sync '+info['id']+(' final' if info['final'] else ''),capture=True)
   git('push','origin',commit+':refs/heads/main')
   git('update-ref','refs/heads/main',commit,parent)
   receipt={'snapshot':info['id'],'commit':commit,'final':info['final'],'published_at':time.time(),'conflicts':result['conflicts']}
   atomic_json(BASE/'published.json',receipt)
   atomic_json(BASE/'tree-acks'/(incoming.name+'.json'),{**receipt,**result,'source_files':info['files']})
   shutil.rmtree(incoming);shutil.rmtree(work)
   print('GIT_TREE_PUBLISHED',json.dumps(receipt),flush=True)
if __name__=='__main__':publish()
