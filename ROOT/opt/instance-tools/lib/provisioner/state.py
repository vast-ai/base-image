"""Per-stage content-hash idempotency for the provisioner.

Each stage computes a SHA-256 hash of its inputs before running.
If the hash matches a stored value, the stage is skipped.
Hashes are stored in STATE_DIR as individual files.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat

log = logging.getLogger("provisioner")

# Overridable so a caller can run the provisioner WITHOUT touching the state the
# real provisioning run depends on. base/13-provisioner-selftest.sh needs exactly
# that: the instance test suite runs at boot stage 70 and the customer's
# provisioning at stage 75, so a self-test sharing this directory would mark
# stages complete that the real run had not performed yet, and the real run would
# then skip them. The hashes are content-keyed, so the collision needs matching
# content to bite — but "usually the hashes differ" is not a property to rest a
# customer's provisioning on when a separate directory costs one line.
#
# Deliberately not routed through _apply_env_overrides: that applies to a loaded
# manifest, and this has to be known before any manifest is read.
#
# VALIDATED, because this is a production code path and not a test-only seam.
# `clear_all_state()` does `shutil.rmtree(STATE_DIR)` and is reachable from
# `provisioner --force`, so an unvalidated override makes
# `PROVISIONER_STATE_DIR=/etc provisioner --force` a recursive delete of /etc as
# root. Anyone who can set this can usually also set PROVISIONING_POST_COMMANDS,
# so this is not a privilege boundary — but a knob added for a test's benefit
# should not hand an operator an `rm -rf` on a typo, and the README describing
# it as "relocate the directory" is exactly how that typo happens.
_DEFAULT_STATE_DIR = "/.provisioner_state"

# Refused outright: the top-level paths whose loss ends the instance. This is a
# blocklist of exact roots, NOT a containment rule — `/etc/ssl`, `/var/lib`,
# `/opt/instance-tools` all pass it. Containment is not what protects a foreign
# directory from `--force`; the OWNERSHIP MARKER is (see mark_stage_complete and
# clear_all_state). The marker is planted only in a directory the provisioner
# itself created, so a directory it was merely pointed at is never adopted and
# never deleted. This blocklist is a cheap first cut that stops the most obvious
# typos before the marker logic is even reached.
_FORBIDDEN_STATE_DIRS = frozenset({
    "/", "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/lib64",
    "/opt", "/root", "/home", "/boot", "/dev", "/proc", "/sys", "/run",
})


def _resolve_state_dir() -> str:
    raw = os.environ.get("PROVISIONER_STATE_DIR", "")
    if not raw:
        return _DEFAULT_STATE_DIR
    path = os.path.normpath(raw)
    if not os.path.isabs(path):
        log.warning("PROVISIONER_STATE_DIR=%r is not absolute, ignoring", raw)
        return _DEFAULT_STATE_DIR
    if path.rstrip("/") in _FORBIDDEN_STATE_DIRS or path == "/":
        log.warning("PROVISIONER_STATE_DIR=%r is a system directory, ignoring", raw)
        return _DEFAULT_STATE_DIR
    return path


STATE_DIR = _resolve_state_dir()

# Written when the provisioner CREATES the state directory, so clear_all_state()
# can tell a directory it owns from one it was merely pointed at. Deleting a tree
# we did not create is the part that turns a bad value into an incident.
_OWNER_MARKER = ".provisioner-state-dir"


def compute_stage_hash(stage_name: str, data: str) -> str:
    """SHA-256 of stage name + serialized input data."""
    content = f"{stage_name}:{data}"
    return hashlib.sha256(content.encode()).hexdigest()


def is_stage_complete(stage_name: str, current_hash: str) -> bool:
    """Check if STATE_DIR/{stage_name}.hash matches current_hash."""
    hash_file = os.path.join(STATE_DIR, f"{stage_name}.hash")
    # O_NOFOLLOW on the READ too: a symlink planted at <stage>.hash by a
    # lower-privileged principal in a writable state dir would otherwise be an
    # equality oracle over any file root can read. Cheap to close alongside the
    # write side.
    try:
        fd = os.open(hash_file, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    with os.fdopen(fd) as f:
        stored = f.read().strip()
    return stored == current_hash


def _plant_owner_marker() -> None:
    """Create the ownership marker, refusing to follow a symlink.

    O_CREAT|O_EXCL|O_NOFOLLOW: a pre-planted symlink (even a DANGLING one, which
    `os.path.exists` reports absent) named `.provisioner-state-dir` would
    otherwise be followed and root would create/write a file at an
    attacker-chosen path outside the state dir. Same hazard the hash-file write
    guards against, three lines away — this is the write that was missed."""
    marker = os.path.join(STATE_DIR, _OWNER_MARKER)
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600)
    except FileExistsError:
        return                                 # already present (or a symlink: refused)
    except OSError as e:
        log.warning("Could not write the ownership marker (%s)", e)
        return
    with os.fdopen(fd, "w") as f:
        f.write("provisioner state directory\n")


def mark_stage_complete(stage_name: str, current_hash: str) -> None:
    """Write current_hash to STATE_DIR/{stage_name}.hash."""
    # Create the directory OURSELVES and plant the ownership marker ONLY when we
    # did. exist_ok=True would silently adopt a pre-existing foreign directory,
    # so the next `clear_all_state()`/--force would rmtree a tree we never made:
    # the marker would be self-granting, protecting for exactly one invocation.
    created = True
    try:
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=False)
    except FileExistsError:
        created = False
    except OSError as e:
        log.warning("Could not create state dir '%s' (%s); the stage will re-run "
                    "next time rather than being skipped", STATE_DIR, e)
        return
    if created:
        _plant_owner_marker()

    # O_NOFOLLOW: a pre-planted symlink named <stage>.hash in a directory some
    # lower-privileged principal can write (images do create uid 1001 with
    # primary group 0) would otherwise be followed and clobbered by root. The
    # write is destructive rather than injective, but it is an arbitrary-file
    # write that did not exist before this directory became relocatable.
    hash_file = os.path.join(STATE_DIR, f"{stage_name}.hash")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    try:
        fd = os.open(hash_file, flags, 0o600)
    except OSError as e:
        log.warning("Could not write state for stage '%s' (%s); the stage will "
                    "re-run next time rather than being skipped", stage_name, e)
        return
    with os.fdopen(fd, "w") as f:
        f.write(current_hash)
    log.debug("Marked stage '%s' complete (hash=%s)", stage_name, current_hash[:12])


def _dir_is_ours(path: str) -> bool:
    """True if the provisioner created this state directory.

    A regular-file ownership marker is proof. `os.lstat` (not `stat`) so a
    SYMLINK named `.provisioner-state-dir` is not accepted as our marker — that
    is forged ownership, which would otherwise turn the delete guard into a way
    to rmtree a directory we did not create.

    Migration: every already-deployed `/.provisioner_state` predates the marker,
    so a directory holding ONLY stage-hash files (and at most the marker name) is
    treated as ours. A foreign directory holding anything else is refused."""
    marker = os.path.join(path, _OWNER_MARKER)
    try:
        st = os.lstat(marker)
    except OSError:
        st = None
    if st is not None and stat.S_ISREG(st.st_mode):
        return True
    try:
        entries = os.listdir(path)
    except OSError:
        return False
    # `bool(entries)` first: `all()` over an EMPTY listing is vacuously True, so
    # without it an empty foreign directory reads as ours and the next --force
    # rmtree's it. Worst shape is a freshly-mounted host-bound volume, where the
    # final rmdir then fails EBUSY and --force dies on a traceback instead of the
    # clean refusal this guard promises.
    return bool(entries) and all(
        e == _OWNER_MARKER or e.endswith(".hash") for e in entries)


def clear_all_state() -> bool:
    """Remove STATE_DIR entirely (for --force or manifest version change).

    Refuses to delete a directory the provisioner did not create, and RETURNS
    whether it cleared. `--force` is the one path that turns a wrong
    PROVISIONER_STATE_DIR into a recursive delete as root, so the caller must be
    able to see a refusal rather than silently carry on and skip stages against
    stale hashes."""
    if not os.path.isdir(STATE_DIR):
        return True                            # nothing to clear
    if not _dir_is_ours(STATE_DIR):
        log.warning("Refusing to clear %s: it has no %s marker and holds files the "
                    "provisioner did not write, so it was not created by us",
                    STATE_DIR, _OWNER_MARKER)
        return False
    shutil.rmtree(STATE_DIR)
    log.info("Cleared all provisioner state")
    return True
