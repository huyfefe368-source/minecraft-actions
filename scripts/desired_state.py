#!/usr/bin/env python3
"""Fail-closed durable desired-state reader for Minecraft runner."""
import argparse
import json
import subprocess

def running(remote="drive:Minecraft-Hosting"):
    try:
        result=subprocess.run(
            ["rclone","cat",remote.rstrip("/")+"/metadata/desired-state.json"],
            capture_output=True,text=True,timeout=30,check=False)
        if result.returncode or len(result.stdout)>4096:
            return False
        state=json.loads(result.stdout)
        return isinstance(state,dict) and state.get("desired")=="running"
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return False

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=["check"])
    p.add_argument("--drive",default="drive:Minecraft-Hosting")
    args=p.parse_args()
    if running(args.drive):
        print("DESIRED_STATE=running")
    else:
        print("DESIRED_STATE=stopped_or_unavailable")
        raise SystemExit(1)
