"""File writer -- cloud-init style write_files."""

from __future__ import annotations

import grp
import logging
import os
import pwd

from ..schema import FileWrite

log = logging.getLogger("provisioner")


def _parse_owner(owner: str) -> tuple[int, int]:
    """Parse 'user:group' or 'user' into (uid, gid).

    Raises KeyError if user or group doesn't exist.
    """
    if ":" in owner:
        user, group = owner.split(":", 1)
    else:
        user = owner
        group = ""

    pw = pwd.getpwnam(user)
    uid = pw.pw_uid

    if group:
        gid = grp.getgrnam(group).gr_gid
    else:
        gid = pw.pw_gid

    return uid, gid


def write_files(
    files: list[FileWrite],
    label: str = "write_files",
    dry_run: bool = False,
) -> None:
    """Write files to disk with specified permissions and ownership.

    Creates parent directories as needed. Permissions are parsed as
    octal from a string (e.g. "0644", "0755").
    """
    if not files:
        log.info("No files to write (%s)", label)
        return

    for entry in files:
        if not entry.path:
            log.warning("Skipping file write with empty path")
            continue

        if dry_run:
            content_preview = entry.content[:80]
            if len(entry.content) > 80:
                content_preview += "..."
            log.info("[DRY RUN] Would write %s (mode=%s, owner=%s): %s",
                     entry.path, entry.permissions, entry.owner or "default",
                     repr(content_preview))
            continue

        # Create parent directories
        parent = os.path.dirname(entry.path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        # Write content
        with open(entry.path, "w") as f:
            f.write(entry.content)

        # Set permissions
        mode = int(entry.permissions, 8)
        os.chmod(entry.path, mode)

        # Set ownership if specified
        if entry.owner:
            try:
                uid, gid = _parse_owner(entry.owner)
                os.chown(entry.path, uid, gid)
                log.info("Wrote %s (mode=%s, owner=%s)", entry.path, entry.permissions, entry.owner)
            except (KeyError, PermissionError) as e:
                log.warning("Wrote %s but could not set owner %s: %s",
                            entry.path, entry.owner, e)
        else:
            log.info("Wrote %s (mode=%s)", entry.path, entry.permissions)
