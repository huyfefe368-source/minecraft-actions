#!/usr/bin/env python3
"""Fail-closed durable desired-state reader for Minecraft runner."""
import argparse
import json
import subprocess
import time

def read_once(remote="drive:Minecraft-Hosting"):
    try:
        result=subprocess.run(
            ["rclone","cat",remote.rstrip("/")+"/metadata/desired-state.json"],
            capture_output=True,text=True,timeout=30,check=False)
        if result.returncode:
            return None
        if len(result.stdout)>4096:
            return "invalid"
        state=json.loads(result.stdout)
        if isinstance(state,dict) and state.get("desired") in ("running","stopped"):
            return state["desired"]
        return "invalid"
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return None

def running(remote="drive:Minecraft-Hosting"):
    for attempt in range(3):
        state=read_once(remote)
        if state == "running":
            return True
        if state in ("stopped", "invalid"):
            return False
        # Fail closed on transient Google API outage but tolerate brief 5xx.
        if attempt < 2:
            time.sleep(2)
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
