# Minecraft Hosting: GitHub Actions + Drive + VPS control panel

Architecture:
- GitHub Actions ubuntu-24.04 public repo: 4 vCPU, 16 GB RAM, temporary 14 GB SSD; Java 25, Paper 26.3, JVM heap 12 GB.
- VPS: existing TCP gateway public 25565 -> loopback SSH reverse tunnel 25566. A second SSH reverse tunnel port 25567 supplies authenticated console commands and logs.
- Google Drive: Minecraft-Hosting/live is the persistence copy; Minecraft-Hosting/backups stores dated compressed snapshots. Do not store world files in Git.
- Panel: Python stdlib, loopback 127.0.0.1:8766; GitHub Actions start/stop, logs, console, recent runs, Drive file editor (when stopped), backup browser.

## Pending Google authorization
The Google Drive connector in ChatGPT cannot transfer its OAuth credentials to a GitHub Actions runner. Authorize rclone separately with the same Google account as the connected Drive. Prefer your own Google Cloud OAuth client ID and client secret.

On a trusted computer install rclone, run rclone config and create a remote named drive with access to Google Drive. Test:

    rclone lsd drive:Minecraft-Hosting

Securely copy the FULL rclone.conf file to VPS (never paste OAuth tokens into chat). On VPS, install rclone and save config as /root/.config/rclone/rclone.conf, mode 600. Then use:

    base64 -w0 /root/.config/rclone/rclone.conf | gh secret set RCLONE_CONFIG_B64 -R huyfefe368-source/minecraft-actions

Ensure rclone on VPS can list the folder:

    rclone lsd drive:Minecraft-Hosting

Do not run hosting workflow until RCLONE_CONFIG_B64 is present; it intentionally fails before launching world if Drive is unavailable.

## Panel login

Panel is bound only to 127.0.0.1, not public HTTP. From a trusted computer:

    ssh -L 8766:127.0.0.1:8766 root@160.191.51.148

Visit http://127.0.0.1:8766 ; username admin. Password is stored at /etc/minecraft-panel/password on VPS (retrieve securely via SSH). For external access, configure a HTTPS domain with proper authentication; do not expose port 8766 over plain HTTP.

    systemctl status minecraft-panel
    journalctl -u minecraft-panel -n 50

## Operational notes

- Initial Drive folder exists, with empty live and backups subfolders. First run creates a new world; later runs restore from live.
- Backups take a best-effort snapshot using save-off, save-all flush, local rsync, then save-on; upload dated compressed snapshot first, then update live.
- Backups scheduled every 15 minutes and at graceful shutdown. Abrupt runner loss can lose changes since last completed upload.
- GitHub-hosted job has a 6-hour maximum execution limit. Workflow ends play session at approximately 5 hours; not a 24/7 host.
- Runner 14 GB temporary SSD bounds maximum active world size even with 5 TB Drive. Snapshots need extra temporary space.
- Stop sends in-game stop, allowing final snapshot. Do not cancel workflow unless necessary.
- Drive edits do not apply to a running world; panel refuses edits while run active.
- Manual recovery currently requires extracting selected backup to live while stopped. Automatic restore UX not implemented.
- No runtime world data goes into Git; old repository-storage test retired.
