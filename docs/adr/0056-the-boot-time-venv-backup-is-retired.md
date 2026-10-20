# ADR 0056 — The boot-time venv backup is retired

- **Status:** Accepted
- **Date:** 2026-10-20
- **Decision owner:** Rob Ballantyne

## Context
`venv-backup.sh` wrote `pip freeze` of every venv under `/venv` to
`$WORKSPACE/.venv-backups/$CONTAINER_ID/`, keeping the newest `VENV_BACKUP_COUNT` (48). It was
scheduled every 30 minutes by cron and also run at every boot. In December 2025 the cron entry
was removed and the script marked "Deprecated. no longer auto-enabled", but the boot run (boot
stage `48-venv-backup.sh`, carried over from the pre-split boot script) stayed.

What remained did not do the job its documentation described:

- **It delayed every app.** The stage ran synchronously before supervisord, so every boot waited
  for a `pip freeze` before any app, the portal or Caddy started: 0.6 s (A1111 template) to 1.6 s
  (ComfyUI template) measured under runc. `pip freeze` reads the metadata of every installed
  distribution, the kind of file-heavy work that is slower again under gVisor (ADR 0055).
- **Its snapshot was the wrong one.** The documented use is moving to a new instance: copy
  `$WORKSPACE`, then `pip install -r venv-main-latest.txt`. A snapshot taken at boot is the
  state before the session's changes, which are exactly what the move should carry. On a first
  boot it is the image's own state (stage 48 ran before provisioning), which a new instance of
  the same image already has.
- **It recorded nothing on synced instances.** It found venvs with `find -type d`, which skips
  symlinks; with environment sync on, `/venv/*` are symlinks, so it wrote no snapshot and logged
  success.
- **The docs promised a different feature:** backups every 30 minutes, 24 hours of undo, at a
  path (`.venv-backup`) the script never wrote.

## Options considered
1. **Retire it fully (chosen).** Remove the boot stage and the script, drop `VENV_BACKUP_COUNT`,
   and document the manual step: `pip freeze > /workspace/venv-main.txt` on the source before
   the transfer, `pip install -r` on the destination. Zero boot cost; the documented move
   becomes correct, because the list is taken when the user is ready to move.
2. **Keep the boot snapshot in the background.** Removes the boot delay, but keeps the wrong
   snapshot, and a background `pip freeze` races the provisioner installing into the same venv
   (stage 75), so the snapshot is pre-, post- or half-provisioned by timing. Rejected.
3. **Restore the 30-minute schedule** the docs promised. Serves the move better than a boot
   snapshot, but adds a periodic process to every instance for a feature with no evidence of
   use, and still needs the symlink fix and output checks. Rejected.
4. **Keep the boot run but switch to `uv pip freeze`** (0.05-0.1 s). Cheapest change, but keeps
   the boot dependency and the wrong snapshot, and its output is not a drop-in for the documented
   `pip install -r` restore: it also pins pip itself and normalises names. Rejected.
5. **Keep the script as a manual tool** (design-review suggestion). A one-line `pip freeze` does
   the same job without a script to maintain against changing `/venv` layouts (conda, the
   external-image venv mirror of ADR 0048). Rejected by the owner: nothing is left that needs it.

## Decision
The boot stage `ROOT/etc/vast_boot.d/48-venv-backup.sh` and `ROOT/opt/instance-tools/bin/venv-backup.sh`
are removed, together with every reference: `README.template.md`, the derivative READMEs that
described the backups or the move, `CONTRIBUTING.md`, and `tools/convert-non-vast-image.sh`.
The READMEs document the manual `pip freeze` step instead.

## Binding conditions
1. No user-facing doc may describe automatic venv backups, `.venv-backups`, or `VENV_BACKUP_COUNT`.
2. The move instructions take the package list on the source, at transfer time.

## Consequences
- Every boot reaches supervisord sooner, under both runtimes.
- Existing `$WORKSPACE/.venv-backups/` directories are left alone; nothing deletes them.
- A user who relied on the automatic file without reading the new docs finds no new snapshot.
  The move instructions now say what to run instead.
- One less tool to keep working as venv layouts change.

## What would reverse this
Evidence that users depend on automatic snapshots (support requests, a restore flow that needs
history rather than a list taken at transfer time). The replacement would then be a periodic
snapshot that follows symlinked venvs and waits for provisioning, not the boot run.
