#!/bin/bash
# Test: NCCL is present, loadable and usable by torch.distributed — on ONE GPU.
#
# This is the IMAGE-owned half of the collectives surface, split out from the
# multi-GPU matrix in 41-nccl-collectives.sh so that it can run — and therefore
# be REQUIRED — on every cell (ADR 0021 decision 5).
#
# The distinction is the whole point. `init_process_group('nccl', world_size=1)`
# exercises everything we ship and control:
#   * libnccl present on the loader path and ABI-matched to the torch wheel
#   * ncclCommInitRank succeeds against the driver actually on the box
#   * torch's distributed extension was built with NCCL support at all
# That is what breaks when a torch/CUDA-backend pairing is wrong — an image
# defect, ours to fix, and one that a 2-GPU rental is not needed to find.
#
# What genuinely needs a second GPU is the TRANSPORT (shared memory, P2P,
# NVLink), which is a property of the HOST. Per ADR 0020 a QA cell may not block
# a promotion on that, so it lives in its own file which skips below 2 GPUs.
#
# Before this split there was no such separation: the collectives block sat
# inside 10-torch-core.sh behind `device_count > 1` and fell through to a bare
# `echo "skip: ..."` on one GPU, so the enclosing test passed either way. Renting
# two GPUs would have produced a green cell that certified nothing.
# TEST_TIMEOUT=1200
# Sized for the VENV COUNT, not for one venv. This test loops over every
# torch venv in /venv/; the `multi` image ships three, so base's single-venv
# timing does not transfer. Read by runner.sh (per-test header beats the
# INSTANCE_TEST_DEFAULT_TIMEOUT the workflow pushes). Under ADR 0020 a
# timeout is inconclusive-and-retried rather than a block, so an under-sized
# value costs real money in re-rentals instead of producing a false red.
source "$(dirname "$0")/../lib.sh"

has_gpu || test_skip "no GPU detected"

# ── Discover torch venvs ──────────────────────────────────────────────
#
# Every venv is checked, not just the first. The multi image ships three, and a
# per-venv NCCL defect (one venv's wheel built against a different CUDA) is
# exactly the class this catches — testing only TORCH_VENVS[0] would leave the
# other two unexercised while reporting the image green.

TORCH_VENVS=()
for venv_dir in /venv/*/; do
    [[ -f "${venv_dir}bin/activate" ]] || continue
    if "${venv_dir}bin/python3" -c "import torch" 2>/dev/null; then
        TORCH_VENVS+=("$venv_dir")
    fi
done

[[ ${#TORCH_VENVS[@]} -gt 0 ]] || test_skip "no venvs with torch found in /venv/"

# A free ephemeral port per run. The old harness hard-coded 29500, so a retry
# after a hung rank collided with the corpse of the previous attempt and hung to
# the per-test timeout — converting host flake into a second failure (ADR 0020).
MASTER_PORT=$(python3 -c "
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
")
echo "  rendezvous port: ${MASTER_PORT}"

for venv_dir in "${TORCH_VENVS[@]}"; do
    venv_name=$(basename "$venv_dir")
    echo ""
    echo "  [${venv_name}] single-rank NCCL init"
    # world_size=1 needs no second device and no transport, so this is
    # deterministic on any single-GPU box.
    "${venv_dir}bin/python3" - "$MASTER_PORT" <<'NCCL_INIT' 2>&1 || \
        fail_later "nccl-init" "[${venv_name}] NCCL unusable from torch.distributed"
import os
import sys

import torch
import torch.distributed as dist

os.environ.setdefault("MASTER_ADDR", "127.0.0.1")
os.environ["MASTER_PORT"] = sys.argv[1]
os.environ.setdefault("RANK", "0")
os.environ.setdefault("WORLD_SIZE", "1")

if not torch.cuda.is_available():
    print("  FAIL: torch reports no CUDA device", flush=True)
    sys.exit(1)

# Distinguish "torch was built without NCCL" from "NCCL is broken at runtime".
# The first is a build-time packaging defect and the message should say so.
if not dist.is_nccl_available():
    print("  FAIL: torch.distributed reports NCCL unavailable "
          "(wheel built without NCCL support)", flush=True)
    sys.exit(1)

torch.cuda.set_device(0)
dist.init_process_group("nccl", rank=0, world_size=1)
try:
    # A real collective, not just init: init can succeed while the communicator
    # is unusable. On world_size=1 this is a no-op mathematically, but it still
    # drives ncclAllReduce through the same code path.
    t = torch.tensor([7.0], device="cuda:0")
    dist.all_reduce(t, op=dist.ReduceOp.SUM)
    if t.item() != 7.0:
        print(f"  FAIL: single-rank all_reduce returned {t.item()}, expected 7.0", flush=True)
        sys.exit(1)
    print(f"  NCCL {'.'.join(str(v) for v in torch.cuda.nccl.version())}: "
          f"init + all_reduce ok", flush=True)
finally:
    dist.destroy_process_group()
NCCL_INIT
done

report_failures
test_pass "NCCL usable from torch.distributed across ${#TORCH_VENVS[@]} venv(s)"
