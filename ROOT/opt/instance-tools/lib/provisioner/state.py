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
STATE_DIR = os.environ.get("PROVISIONER_STATE_DIR", "/.provisioner_state")


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
    os.makedirs(STATE_DIR, exist_ok=True)
    hash_file = os.path.join(STATE_DIR, f"{stage_name}.hash")
    with open(hash_file, "w") as f:
        f.write(current_hash)
    log.debug("Marked stage '%s' complete (hash=%s)", stage_name, current_hash[:12])


def clear_all_state() -> None:
    """Remove STATE_DIR entirely (for --force or manifest version change)."""
    if os.path.isdir(STATE_DIR):
        shutil.rmtree(STATE_DIR)
        log.info("Cleared all provisioner state")
