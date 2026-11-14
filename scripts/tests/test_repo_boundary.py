"""The public source repository must never carry panel code or game data."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def test_public_repo_contains_only_runner_code():
    tracked = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines()
    disallowed = ("panel/", "minecraft-data/", "server/", "world/",
                  "world_nether/", "world_the_end/", "plugins/", "backups/")
    assert not [p for p in tracked if p.startswith(disallowed)]
    assert "HOSTING.md" not in tracked
    assert ".github/workflows/minecraft-hosting.yml" in tracked
    assert "scripts/run_server.sh" in tracked
    assert "scripts/world_store.py" in tracked


def test_runtime_secrets_and_worlds_are_gitignored():
    for path in ("server/world/region/r.0.0.mca",
                 "minecraft-data/server/server.properties",
                 "plugins/SomePlugin.jar", "private/rclone.conf",
                 ".env", "backups/first.tar.gz"):
        result = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=ROOT, check=False,
        )
        assert result.returncode == 0, path
