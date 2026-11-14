# Minecraft Actions runner

This repository exists **only to run Minecraft Paper on GitHub Actions**. Game runtime files, player data, worlds, plugin binaries, backups, and the management panel must never be committed here.

- Live game process: `.github/workflows/minecraft-hosting.yml` and `scripts/run_server.sh`
- Runner console/metrics tunnel: `scripts/runner_console.py`
- Verified Drive snapshot/restore: `scripts/world_store.py`
- Server storage: Google Drive `Minecraft-Hosting/live`, `Minecraft-Hosting/backups`, and `Minecraft-Hosting/metadata`
- Public Minecraft gateway: VPS TCP `25565`. Panel source lives **only on the VPS** at `/opt/minecraft-panel`.

Do not start the new hosting workflow until `RCLONE_CONFIG_B64` is authorized and stored as a GitHub secret; runner SSH and EULA secrets/variables must be verified as well. GitHub Actions is time-limited and not a substitute for a guaranteed 24/7 host.
