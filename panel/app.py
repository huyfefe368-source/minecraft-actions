#!/usr/bin/env python3
"""Local-only Minecraft hosting panel; put HTTPS + access control in front before publishing."""
import base64
import hmac
import json
import os
import re
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO = "huyfefe368-source/minecraft-actions"
WORKFLOW = "minecraft-hosting.yml"
DRIVE = "drive:Minecraft-Hosting"
PASSWORD_FILE = Path("/etc/minecraft-panel/password")
CONTROL_FILE = Path("/etc/minecraft-panel/control-token")
WEB = Path(__file__).with_name("index.html")
SAFE_NAME = re.compile(r"^[^/\x00-\x1f]+$")

def run(args, timeout=20, input_bytes=None):
    try:
        p = subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(type(exc).__name__)
    if p.returncode:
        raise RuntimeError(p.stderr.decode("utf-8", "replace")[:450])
    return p.stdout.decode("utf-8", "replace")

def active():
    results = json.loads(run(["gh", "run", "list", "-R", REPO, "-w", WORKFLOW,
                              "--limit", "5", "--json", "databaseId,status,conclusion,createdAt,url"]))
    return results, next((r for r in results if r["status"] in ("in_progress", "queued", "waiting", "pending", "requested")), None)

def control(op, command=""):
    token = CONTROL_FILE.read_text().strip()
    with socket.create_connection(("127.0.0.1", 25567), timeout=3) as conn:
        conn.settimeout(3)
        payload = {"token": token, "op": op}
        if op == "command":
            payload["command"] = command
        conn.sendall((json.dumps(payload) + "\n").encode())
        stream = conn.makefile("rb")
        return json.loads(stream.readline())

def safe_path(raw):
    parts = [part for part in raw.split("/") if part]
    if any(part in (".", "..") or not SAFE_NAME.fullmatch(part) for part in parts):
        raise ValueError("Invalid file path")
    return "/".join(parts)

def drive_available():
    return Path("/root/.config/rclone/rclone.conf").exists() and bool(shutil_which("rclone"))

def shutil_which(binary):
    from shutil import which
    return which(binary)

def drive_target(subpath):
    clean = safe_path(subpath)
    return DRIVE + "/live" + ("/" + clean if clean else "")

class Handler(BaseHTTPRequestHandler):
    server_version = "MinecraftPanel/1.0"
    def log_message(self, fmt, *args):
        pass

    def auth(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(auth[6:], validate=True).decode("utf-8")
            username, password = raw.split(":", 1)
        except (ValueError, UnicodeError):
            return False
        expected = PASSWORD_FILE.read_text().strip()
        return hmac.compare_digest(username, "admin") and hmac.compare_digest(password, expected)

    def send(self, code, data, ctype="application/json"):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype == "application/json" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def handle_request(self, method):
        if not self.auth():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Minecraft Hosting"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path = urlsplit(self.path)
        try:
            if method == "GET" and path.path == "/":
                return self.send(200, WEB.read_bytes(), "text/html")
            if method == "GET" and path.path == "/app.js":
                return self.send(200, WEB.with_name("app.js").read_bytes(), "text/javascript")
            if method == "GET" and path.path == "/api/status":
                runs, current = active()
                try:
                    live = control("logs")
                    gateway = bool(live.get("ok"))
                except (OSError, ValueError, RuntimeError):
                    gateway = False
                return self.send(200, {"runs": runs, "active": current, "console_connected": gateway,
                                        "drive_connected": drive_available()})
            if method == "GET" and path.path == "/api/logs":
                return self.send(200, control("logs"))
            if method == "GET" and path.path == "/api/files":
                if not drive_available():
                    return self.send(200, {"configured": False, "files": []})
                directory = parse_qs(path.query).get("path", [""])[0]
                target = drive_target(directory)
                files = json.loads(run(["rclone", "lsjson", target, "--max-depth", "1"], timeout=25))
                return self.send(200, {"configured": True, "path": directory, "files": files})
            if method == "GET" and path.path == "/api/file":
                if not drive_available():
                    return self.send(409, {"error": "Drive not configured"})
                filepath = safe_path(parse_qs(path.query).get("path", [""])[0])
                if not filepath:
                    return self.send(400, {"error": "Missing file path"})
                target = drive_target(filepath)
                meta = json.loads(run(["rclone", "lsjson", target, "--stat"], timeout=20))
                if meta.get("IsDir") or meta.get("Size", 0) > 250000:
                    return self.send(413, {"error": "Only text files below 250 KB can be edited"})
                return self.send(200, {"path": filepath,
                                       "content": run(["rclone", "cat", target], timeout=20)})
            if method == "GET" and path.path == "/api/backup":
                if not drive_available():
                    return self.send(200, {"configured": False, "files": []})
                items = json.loads(run(["rclone", "lsjson", DRIVE + "/backups", "--max-depth", "1"], timeout=25))
                return self.send(200, {"configured": True, "files": items})
            if method == "POST":
                if self.headers.get("X-Panel-Request") != "1":
                    return self.send(403, {"error": "CSRF guard"})
                if int(self.headers.get("Content-Length", "0")) > 300000:
                    return self.send(413, {"error": "Body too large"})
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                if path.path == "/api/start":
                    _, current = active()
                    if current:
                        return self.send(409, {"error": "Already running or queued"})
                    secrets = run(["gh", "secret", "list", "-R", REPO])
                    if "RCLONE_CONFIG_B64" not in secrets:
                        return self.send(409, {"error": "Drive OAuth secret not configured"})
                    run(["gh", "workflow", "run", WORKFLOW, "-R", REPO])
                    return self.send(200, {"ok": True, "message": "GitHub Actions dispatched"})
                if path.path == "/api/restore":
                    _, current = active()
                    if current:
                        return self.send(409, {"error": "Server must be stopped"})
                    backup = payload.get("backup", "")
                    if not isinstance(backup, str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9]+\.tar\.gz", backup):
                        return self.send(400, {"error": "Invalid backup name"})
                    run(["gh", "workflow", "run", WORKFLOW, "-R", REPO,
                         "-f", "restore_backup=" + backup])
                    return self.send(200, {"ok": True, "message": "Restoration dispatched: " + backup})
                if path.path == "/api/stop":
                    response = control("command", "stop")
                    return self.send(200, response)
                if path.path == "/api/command":
                    cmd = payload.get("command")
                    if not isinstance(cmd, str) or not 1 <= len(cmd) <= 150 or "\n" in cmd or "\r" in cmd:
                        return self.send(400, {"error": "Invalid command"})
                    return self.send(200, control("command", cmd))
                if path.path in ("/api/savefile", "/api/deletefile"):
                    _, current = active()
                    if current:
                        return self.send(409, {"error": "Edit Drive files only when the server is stopped"})
                    if not drive_available():
                        return self.send(409, {"error": "rclone is not connected on VPS"})
                    filepath = safe_path(payload.get("path", ""))
                    if not filepath:
                        return self.send(400, {"error": "Missing path"})
                    target = drive_target(filepath)
                    if path.path == "/api/deletefile":
                        run(["rclone", "deletefile", target], timeout=25)
                    else:
                        content = payload.get("content")
                        if not isinstance(content, str) or len(content.encode()) > 250000:
                            return self.send(413, {"error": "Text file too large"})
                        run(["rclone", "rcat", target], timeout=40, input_bytes=content.encode())
                    return self.send(200, {"ok": True})
            return self.send(404, {"error": "Not found"})
        except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
            return self.send(503, {"error": str(exc)[:250]})

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")


if __name__ == "__main__":
    if not PASSWORD_FILE.exists() or not CONTROL_FILE.exists():
        raise SystemExit("Missing /etc/minecraft-panel credentials")
    ThreadingHTTPServer(("127.0.0.1", 8766), Handler).serve_forever()
