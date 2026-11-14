#!/usr/bin/env python3
"""Read panel-selected startup profile and launch a local server jar/script.

No fixed server type, version or memory cap; no shell interpolation.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
from startup_config import StartupStore, validate, startup_file

def prepare(output: Path):
    config = StartupStore().read()
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(validate(config),ensure_ascii=False))
    print("STARTUP_PROFILE_LOADED software="+config["software"]+" java="+config["java_version"])

def launch(config_file: Path, root: Path):
    profile=validate(json.loads(config_file.read_text()))
    root=root.resolve(strict=True)
    args=shlex.split(profile["command"])
    resource = root.joinpath(startup_file(profile)).resolve(strict=False)
    if not resource.is_relative_to(root) or not resource.is_file():
        raise ValueError("STARTUP_FILE_MISSING: upload "+startup_file(profile)+" to Drive/live")
    if args[0]=="java":
        env_name="JAVA_HOME_"+profile["java_version"]+"_X64"
        home=os.environ.get(env_name)
        if not home:
            raise ValueError("JAVA_VERSION_UNAVAILABLE: "+env_name)
        binary=Path(home)/"bin/java"
        if not binary.is_file():
            raise ValueError("JAVA_BINARY_MISSING")
        args[0]=str(binary)
    else:
        binary=shutil.which(args[0])
        if not binary:
            raise ValueError("STARTUP_SHELL_UNAVAILABLE")
        args[0]=binary
    os.chdir(root)
    print("STARTUP_LAUNCH software="+profile["software"]+
          " java="+profile["java_version"]+" file="+resource.name,flush=True)
    os.execv(args[0],args)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("action", choices=("prepare","launch"))
    p.add_argument("--output", type=Path)
    p.add_argument("--config",type=Path)
    p.add_argument("--root",type=Path)
    args=p.parse_args()
    try:
        if args.action=="prepare":
            if args.output is None: raise ValueError("Missing config output path")
            prepare(args.output)
        else:
            if args.config is None or args.root is None:raise ValueError("Missing startup input")
            launch(args.config,args.root)
    except (OSError,ValueError,RuntimeError) as error:
        print("STARTUP_ERROR="+str(error),file=sys.stderr)
        raise SystemExit(1)

if __name__=="__main__":main()
