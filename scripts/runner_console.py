#!/usr/bin/env python3
"""Authenticated, loopback-only console API carried over an SSH reverse tunnel."""
import argparse
import asyncio
import hmac
import json
import os
import subprocess
import time
from pathlib import Path

TOKEN = os.environ.get("MC_CONTROL_TOKEN", "")
STARTED = time.monotonic()

def collect_metrics(paper_pid: int) -> dict:
    """Get the Paper process RSS and average CPU usage, never claim host totals."""
    try:
        result = subprocess.run(["ps", "-p", str(paper_pid), "-o", "%cpu=,rss="],
                                check=True, capture_output=True, text=True, timeout=2)
        cpu, rss = result.stdout.split()[:2]
        return {"ok": True, "cpu_percent": float(cpu),
                "memory_bytes": int(rss) * 1024,
                "uptime_seconds": int(time.monotonic() - STARTED),
                "timestamp": time.time()}
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        return {"ok": False, "error": "Paper metrics unavailable"}


async def handle(reader, writer, fifo, logfile, paper_pid):
    try:
        raw = await asyncio.wait_for(reader.readline(), 3)
        if len(raw) > 8192:
            return
        req = json.loads(raw)
        if not TOKEN or not hmac.compare_digest(str(req.get("token", "")), TOKEN):
            response = {"ok": False, "error": "unauthorized"}
        elif req.get("op") == "metrics":
            response = collect_metrics(paper_pid)
        elif req.get("op") == "logs":
            log = logfile.read_bytes()[-18000:].decode("utf-8", "replace") if logfile.exists() else ""
            response = {"ok": True, "logs": log}
        elif req.get("op") == "command":
            cmd = req.get("command")
            if not isinstance(cmd, str) or not 1 <= len(cmd) <= 150 or any(c in cmd for c in "\r\n\0"):
                response = {"ok": False, "error": "invalid command"}
            else:
                if cmd.strip().lower() == "stop":
                    Path(os.environ["RUNNER_TEMP"]).joinpath("mc-stop-requested").touch()
                with fifo.open("w") as f:
                    f.write(cmd + "\n")
                response = {"ok": True}
        else:
            response = {"ok": False, "error": "invalid operation"}
        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()
    except (OSError, ValueError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()

async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fifo", required=True, type=Path)
    p.add_argument("--log", required=True, type=Path)
    p.add_argument("--paper-pid", required=True, type=int)
    args = p.parse_args()
    if not TOKEN:
        raise SystemExit("Missing MC_CONTROL_TOKEN")
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, args.fifo, args.log, args.paper_pid), "127.0.0.1", 25567
    )
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
