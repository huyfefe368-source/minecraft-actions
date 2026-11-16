"""VPS-side atomic tree validation and retryable private Git push."""
import fcntl
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from temp_mc_state import verify

BASE=Path('/srv/mc-temp12111')
REPO=BASE/'tree-repo'

def run(args,**kwargs):
    return subprocess.run(args,check=True,**kwargs)

def publish(lock_path='/run/lock/mc-tree-publish.lock'):
    with open(lock_path,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        for marker in sorted((BASE/'tree-incoming').glob('*/READY')):
            incoming=marker.parent
            info=json.loads((incoming/'manifest.json').read_text())
            verify(incoming/'server',info['files'])
            if 'world/level.dat' not in info['files'] or 'server.jar' not in info['files']:
                raise ValueError('Snapshot is not a complete Minecraft server')
            oversized=[p for p,v in info['files'].items() if v['size']>=100*1024*1024]
            if oversized:
                raise ValueError('GitHub 100 MiB limit; VPS retains snapshot: '+str(oversized))
            run(['git','-C',str(REPO),'pull','--ff-only'])
            run(['rsync','-a','--delete',str(incoming/'server')+'/',str(REPO/'server')+'/'])
            # An external ignore file must never silently omit server files.
            (REPO/'snapshot.json').write_text(json.dumps(info,indent=2))
            run(['git','-C',str(REPO),'add','-f','--all','server','snapshot.json'])
            changed=subprocess.run(['git','-C',str(REPO),'diff','--cached','--quiet']).returncode
            if changed:
                run(['git','-C',str(REPO),'commit','-m','Minecraft full tree '+info['id']+(' final' if info['final'] else ' checkpoint')])
            run(['git','-C',str(REPO),'push','origin','main'])
            sha=subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip()
            receipt={'snapshot':info['id'],'commit':sha,'final':info['final'],'published_at':time.time()}
            tmp=BASE/'published.json.tmp';tmp.write_text(json.dumps(receipt));tmp.replace(BASE/'published.json')
            # Remove only a verified and successfully published staging tree.
            shutil.rmtree(incoming)
            print('GIT_TREE_PUBLISHED',json.dumps(receipt),flush=True)

if __name__=='__main__':publish()
