# Minecraft Persistence Implementation Plan

**Goal:** Preserve full server trees on early Stop/ordinary Cancel and provide an authenticated remote console.

**Architecture:** Independent Actions systemd worker, manifest-verified VPS staging, retryable private Git publisher and HTTPS console.

**Tech Stack:** Python standard library, rsync/SSH, Git, systemd, Caddy.

**Spec:** docs/superpowers/specs/2026-11-16-minecraft-persistence-design.md

## Global Constraints

- Purpur 1.21.11, Java 21; existing VPS Minecraft address.
- All new saves are individual files under private repo `server/`, not archives.
- Credentials never enter source or data repositories.

## Review Focus

- Repeated signals cannot interrupt cleanup.
- Partial staging never replaces a complete snapshot.
- Git push failure retains VPS staging for retry.
- Console commands remain behind authentication and reject multiline requests.
- Normal cancellation must produce a final snapshot; force kill recovers last checkpoint.

## Tasks

- [x] Stop the existing job via tunnel disconnect and verify final archive success.
- [x] Test manifest corruption and symlink rejection; implement inventory and verification.
- [x] Replace archive restore/backup with exact tree restore, 60-second checkpoints and final durable transfer.
- [x] Implement independent worker lifecycle and always() cleanup.
- [x] Implement VPS publisher and authenticated console with loopback SSH tunnel.
- [x] Migrate last successful snapshot, run tests, deploy and verify Git files.
- [x] Run an Actions playtest; verify console Stop and normal Cancel publish final snapshots.

## Validation

Eight unit/integration tests pass on VPS. Console HTTPS rejects unauthenticated requests (401) and accepts authenticated commands (200). Stop run 43861107055 completed successfully and final snapshot 421e00d was published. Cancel run 43861524011 completed cancelled with the always() save step successful; final snapshot 5bbc0af was SHA-256 verified and contains a command sent immediately before cancellation. Initialization cancellation, slow checkpoint interruption, total cleanup deadline and malformed-snapshot quarantine have regression tests. Code review found no remaining P1/P2 blockers after fixes.

Final deployed-code Cancel test: run 43862665214 completed cancelled with stop/save cleanup successful. Final snapshot commit 37c0371 was published, exact manifest verified, and FINAL_CANCEL_TEST console command retained. Server is offline.
