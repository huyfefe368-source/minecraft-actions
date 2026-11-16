# Purpur 1.21.11 playtest

Public workflow repository: huyfefe368-source/minecraft-actions.
Private server data: huyfefe368-source/minecraft-temporary-backups, `server/` on `main`.

Start `temp-purpur-playtest.yml` manually. Java address: `160.191.51.148:25577`.

## Remote console

https://mc-console.160-191-51-148.sslip.io/

HTTPS basic authentication is required. Credentials are stored only in `/root/minecraft-console-login.txt` on the VPS (first line username, second password). Retrieve using your own SSH session. Console shows live logs, accepts Minecraft commands, and has Stop and Save. No OS shell commands are accepted.

## Persistence and stopping

Each complete checkpoint contains all regular server files including jars, libraries, plugins, dotfiles, configuration and worlds. No new tar/zip archives are created. Git cannot represent empty directories; Minecraft recreates them. No files currently exceed the GitHub 100 MiB limit. An oversized file causes a visible error and retains the snapshot on VPS.

The worker flushes world saves, mirrors to VPS every 60 seconds, and the publisher validates SHA-256 before committing. During Stop, Java exits before final snapshot. VPS pushes asynchronously and retries every 20 seconds. A failed Git push retains staging. Check the console for the final commit receipt.

Ordinary Actions Cancel cancels the monitoring step and executes the `always()` stop/save step; the systemd worker is outside the step process tree. Do not use Force cancel while saving. Force cancel, destroyed VMs, failed networking or storage can lose changes since the last complete checkpoint.

The previous archive Release remains available as a migration recovery point. Future jobs restore the individual files from the private Git tree through VPS.

Rejected manifests or oversized snapshots are retained under `/srv/mc-temp12111/tree-rejected/` and do not block later valid trees. Stop interrupts an in-progress checkpoint and initialization promptly; a final cleanup budget reserves time inside the Actions cancellation window. On exhausted cleanup/network budget, recover from the last complete checkpoint.
