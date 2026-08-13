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

# Refused outright. Not an attempt at an exhaustive blocklist — the containment
# rule below is what does the work — just the paths whose loss ends the instance.
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

# Written on first use so `clear_all_state()` can tell a directory it owns from
# one it was merely pointed at. Deleting a tree we did not create is the part
# that turns a bad value into an incident.
_OWNER_MARKER = ".provisioner-state-dir"


def compute_stage_hash(stage_name: str, data: str) -> str:
    """SHA-256 of stage name + serialized input data."""
    content = f"{stage_name}:{data}"
    return hashlib.sha256(content.encode()).hexdigest()


def is_stage_complete(stage_name: str, current_hash: str) -> bool:
    """Check if STATE_DIR/{stage_name}.hash matches current_hash."""
    hash_file = os.path.join(STATE_DIR, f"{stage_name}.hash")
    try:
        with open(hash_file) as f:
            stored = f.read().strip()
        return stored == current_hash
    except FileNotFoundError:
        return False


def mark_stage_complete(stage_name: str, current_hash: str) -> None:
    """Write current_hash to STATE_DIR/{stage_name}.hash."""
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    # Marks the directory as ours, so clear_all_state() can refuse to delete a
    # tree it did not create.
    marker = os.path.join(STATE_DIR, _OWNER_MARKER)
    if not os.path.exists(marker):
        with open(marker, "w") as f:
            f.write("provisioner state directory\n")

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


def clear_all_state() -> None:
    """Remove STATE_DIR entirely (for --force or manifest version change).

    Refuses to delete a directory the provisioner did not create. `--force` is
    the one path that turns a wrong PROVISIONER_STATE_DIR into a recursive
    delete as root, and the marker is what distinguishes "our state" from
    "whatever the operator pointed this at".
    """
    if os.path.isdir(STATE_DIR) and not os.path.exists(
            os.path.join(STATE_DIR, _OWNER_MARKER)):
        log.warning("Refusing to clear %s: no %s marker, so this directory was "
                    "not created by the provisioner", STATE_DIR, _OWNER_MARKER)
        return
    if os.path.isdir(STATE_DIR):
        shutil.rmtree(STATE_DIR)
        log.info("Cleared all provisioner state")
