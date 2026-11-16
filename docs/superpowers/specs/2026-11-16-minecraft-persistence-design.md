# Safe early stop, Git server tree and remote console

The user requested stopping the current playtest, safe early Cancel/Stop, full uncompressed server files in a repository, and a remote Actions Minecraft console.

Keep the public workflow repository and private `minecraft-temporary-backups` data repository separate. Import the final successful archive once; future runs restore and publish `server/` as individual Git files. VPS staging validates exact SHA-256 manifests before commit. Keep pending snapshots until push succeeds. Existing archives remain recovery points.

Run Java under a transient systemd unit independent of the Actions monitoring step. Ordinary Cancel interrupts the monitor; an `always()` final step writes a stop request and waits for shutdown and durable rsync. The VPS publisher pushes independently and retries failures. Checkpoint every 60 seconds after Minecraft acknowledges flush. Force cancellation, VM destruction, disk exhaustion or network outages cannot guarantee zero loss; recover the last complete checkpoint.

Expose a web console over HTTPS with basic authentication, log tail, command entry, Stop and latest published commit. SSH reverse forwarding binds the runner console to VPS loopback only. Keep SSH credentials out of both repositories. Retain the Minecraft IP and Java 21/Purpur 1.21.11.

Approaches considered: signal-only cleanup is too short; job-level always() can ignore Cancel entirely; a service plus always() finalizer provides explicit normal cancellation and durable transfer. GitHub limits individual files to 100 MiB; oversized snapshots remain on VPS and fail visibly rather than disappearing. Use no archives for new persistence.
