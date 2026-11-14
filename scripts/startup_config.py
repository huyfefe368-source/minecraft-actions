"""Panel-owned startup profile stored on Drive, independent of runner YAML."""
from __future__ import annotations
import json
import re
import shlex
import subprocess
from pathlib import PurePosixPath

SUPPORTED = ("8", "11", "17", "21", "25")
DEFAULT = {"java_version": "21", "command": "java -jar server.jar nogui", "software": "Custom"}
PATH = "drive:Minecraft-Hosting/metadata/startup.json"

class StartupError(RuntimeError):
    pass

def validate(data):
    if not isinstance(data, dict):
        raise ValueError("Cấu hình startup không hợp lệ")
    version = data.get("java_version", "21")
    command = data.get("command", DEFAULT["command"])
    software = data.get("software", "Custom")
    if version not in SUPPORTED:
        raise ValueError("Chọn Java 8, 11, 17, 21 hoặc 25")
    if not isinstance(software, str) or len(software) > 40:
        raise ValueError("Tên loại server quá dài")
    if not isinstance(command, str) or not 1 <= len(command) <= 512:
        raise ValueError("Startup dài tối đa 512 ký tự")
    if any(c in command for c in "\r\n\x00;|&<>$"):
        raise ValueError("Startup không hỗ trợ lệnh shell ghép hoặc chuyển hướng")
    try:
        args = shlex.split(command)
    except ValueError:
        raise ValueError("Dấu nháy trong startup không hợp lệ") from None
    if not 2 <= len(args) <= 50:
        raise ValueError("Lệnh startup không hợp lệ")
    if args[0] == "java":
        if args.count("-jar") != 1 or args.index("-jar") + 1 >= len(args):
            raise ValueError("Lệnh Java phải có -jar <file.jar>")
        script = args[args.index("-jar")+1]
        if not script.lower().endswith(".jar"):
            raise ValueError("Phải chọn file .jar")
    elif args[0] in ("bash", "sh"):
        script = args[1]
        if not script.endswith(".sh"):
            raise ValueError("Startup bash/sh phải dùng file .sh")
    else:
        raise ValueError("Chỉ hỗ trợ java -jar, bash run.sh hoặc sh run.sh")
    path = PurePosixPath(script)
    if path.is_absolute() or not path.parts or len(path.parts) > 16 or any(
        x in (".", "..", "") or x.startswith("~") for x in path.parts
    ) or ":" in script or "\\" in script:
        raise ValueError("File startup phải nằm trong server/")
    if any(len(arg) > 256 for arg in args):
        raise ValueError("Tham số startup quá dài")
    return {"java_version": version, "command": command.strip(), "software": software.strip() or "Custom"}

def call(argv, content=None):
    try:
        return subprocess.run(argv, input=content, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=55, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise StartupError("Không thể liên lạc Google Drive") from None

class StartupStore:
    def read(self):
        result = call(["rclone", "cat", PATH])
        if result.returncode:
            check = call(["rclone", "lsf", "drive:Minecraft-Hosting/metadata"])
            if check.returncode or b"startup.json" in check.stdout.splitlines():
                raise StartupError("Không thể đọc file startup trên Drive")
            return dict(DEFAULT)
        try:
            return validate(json.loads(result.stdout))
        except (ValueError, UnicodeError):
            raise StartupError("File startup trên Drive không hợp lệ") from None

    def write(self, data):
        config = validate(data)
        payload = json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode()
        result = call(["rclone", "rcat", PATH], payload)
        if result.returncode:
            raise StartupError("Không thể lưu startup lên Drive")
        return config

def startup_file(data):
    config = validate(data)
    args = shlex.split(config["command"])
    return args[args.index("-jar")+1] if args[0] == "java" else args[1]
