"""Download the pinned Kev adapter and its base model into the HF cache during provisioning, so the first
server start loads from disk instead of pulling ~9-19 GB.

KEV_MODEL is a Hub id pinned with @revision (e.g. jaredpalmer/kev-4b@<sha>). Kev's own Checkpoint resolves it
(downloading the adapter at that revision) and records the base model and the exact base revision the
adapter was trained on; the same revision is fetched here, so kev.serve finds it in the cache.
"""
import os, sys

from huggingface_hub import snapshot_download
from kev.checkpoint import Checkpoint

run = os.environ.get("KEV_MODEL", "")
if "@" not in run:
    sys.exit(f"KEV_MODEL must be a pinned Hub id like jaredpalmer/kev-4b@<revision>, got {run!r}")
ck = Checkpoint(run)
if not ck.meta.base_revision:
    sys.exit(f"{run} does not record a base revision; refusing to fetch an unpinned base")
path = snapshot_download(ck.meta.base, revision=ck.meta.base_revision,
                         allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja", "*.model"])
print(f"prefetched {run} -> base {ck.meta.base}@{ck.meta.base_revision} at {path}")
