"""Keep the Minecraft worker outside the Actions step's process tree.

The always() cleanup step requests a normal stop, then waits for durable transfer.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

def main():
    mode=sys.argv[1]
    base=Path(os.environ['RUNNER_TEMP'])/'purpur-playtest'
    base.mkdir(exist_ok=True)
    unit='mc-purpur-'+os.environ['GITHUB_RUN_ID']+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
    log=base/'worker.log'
    if mode=='start':
        envfile=base/'worker.env'
        # EnvironmentFile values are quoted; never log this file.
        values={k:os.environ[k] for k in ('RUNNER_TEMP','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','TEMP_PURPUR_SSH_KEY','PATH') if k in os.environ}
        with envfile.open('w') as f:
            os.chmod(envfile,0o600)
            for k,v in values.items():
                escaped=v.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')
                # SSH key is multiline: pass it in a separate private file instead.
                if k=='TEMP_PURPUR_SSH_KEY':continue
                f.write(k+'="'+escaped+'"\n')
        key=base/'credential'
        with key.open('w') as f:
            os.chmod(key,0o600);f.write(values['TEMP_PURPUR_SSH_KEY'])
        envfile.write_text(envfile.read_text()+'TEMP_PURPUR_KEY_FILE="'+str(key)+'"\n')
        subprocess.run(['sudo','systemd-run','--unit='+unit,'--property=Type=exec',
            '--property=RuntimeMaxSec=18500','--property=TimeoutStopSec=240',
            '--property=EnvironmentFile='+str(envfile),
            '--property=WorkingDirectory='+os.getcwd(),
            '--property=StandardOutput=append:'+str(log),
            '--property=StandardError=append:'+str(log),
            '/usr/bin/python3',str(Path('scripts/temp-purpur-runner.py').resolve()),sys.argv[2]],check=True)
    else:
        (base/'stop-requested').touch()
    deadline=time.monotonic()+(280 if mode=='stop' else 18500)
    offset=0
    while time.monotonic()<deadline:
        if log.exists():
            with log.open() as f:
                f.seek(offset);print(f.read(),end='',flush=True);offset=f.tell()
        state=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True).stdout.strip()
        if state not in ('active','activating','deactivating'):
            result=int((base/'result').read_text()) if (base/'result').exists() else 1
            if mode=='stop':
                for name in ('worker.env','credential','ssh_key'):(base/name).unlink(missing_ok=True)
            return result
        time.sleep(2)
    print('Worker still saving; check VPS snapshot receipt',flush=True)
    return 1

if __name__=='__main__':sys.exit(main())
