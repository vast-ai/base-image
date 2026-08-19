#!/bin/bash
# Test: multi-GPU NCCL collectives — all_reduce, broadcast, all_gather.
#
# Moved here verbatim in behaviour from 10-torch-core.sh, where it lived behind
# `device_count > 1` and fell through on a single GPU to
#
#     echo "  skip: multi-GPU comms (single GPU)"
#
# — a bare echo, so the ENCLOSING test still passed. That made the collectives
# matrix invisible to INSTANCE_TEST_REQUIRE_PASS, which matches on test states
# and cannot see a branch inside a passing test. Renting a 2-GPU box bought a
# green cell that certified nothing.
#
# As its own file it does the honest thing: `test_skip` (exit 77) below two
# GPUs, which is a state the runner and qa_verdict.py can both see. That makes
# it NAMEABLE in a multi-GPU cell's required list — the prerequisite for ever
# gating collectives, which ADR 0021 defers rather than adopts.
#
# FAULT DOMAIN (ADR 0020): what this exercises is the TRANSPORT — shared memory,
# P2P, NVLink — which belongs to the host, not the image. On consumer cards with
# no NVLink, NCCL falls back to the /dev/shm path, and Vast exposes no control
# over shm size. So do NOT add this to a gating required list without first
# measuring shm on the offer tier the selector actually picks; a failure here is
# as likely to be the box as the image, and blocking a promotion on it would
# charge the wrong party.
source "$(dirname "$0")/../lib.sh"

has_gpu || test_skip "no GPU detected"

TORCH_VENVS=()
for venv_dir in /venv/*/; do
    [[ -f "${venv_dir}bin/activate" ]] || continue
    if "${venv_dir}bin/python3" -c "import torch" 2>/dev/null; then
        TORCH_VENVS+=("$venv_dir")
    fi
done

[[ ${#TORCH_VENVS[@]} -gt 0 ]] || test_skip "no venvs with torch found in /venv/"

_device_count=$("${TORCH_VENVS[0]}bin/python3" -c \
    "import torch; print(torch.cuda.device_count())" 2>/dev/null || echo 0)

# THE line that makes this test honest. `test_skip` exits 77, which the runner
# records as `skipped` and the required-pass gate treats as not-passed — so a
# cell that names this test and lands on one GPU FAILS rather than quietly
# reporting success. The old `echo` could not do that.
[[ "$_device_count" -gt 1 ]] || \
    test_skip "multi-GPU collectives need >= 2 GPUs (found ${_device_count})"

# Select the venv by NAME where possible rather than relying on glob order:
# /venv/* sorts `main` first today, which is correct by accident of the layout
# in Dockerfile.multi-torch and would silently change the venv under test the
# day one is named `alt` or `base`.
NCCL_VENV="${TORCH_VENVS[0]}"
for venv_dir in "${TORCH_VENVS[@]}"; do
    [[ "$(basename "$venv_dir")" == "main" ]] && NCCL_VENV="$venv_dir" && break
done
echo "  venv under test: $(basename "$NCCL_VENV")"
echo "  NOTE: collectives are exercised in ONE venv; a multi image's other"
echo "        torch venvs are covered by 40-nccl-init.sh, not by this matrix."

# A free ephemeral port. Hard-coding 29500 meant a retry after a hung rank
# collided with the previous attempt and hung to the per-test timeout.
MASTER_PORT=$(python3 -c "
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
")

# mp.spawn needs a real script file — it pickles the worker function and the
# spawned subprocess re-imports __main__ to unpickle it. With `python3 -c` there
# is no __main__ file, so unpickling fails.
NCCL_SCRIPT="/tmp/test_nccl_collectives.py"
cat > "$NCCL_SCRIPT" <<'NCCL_EOF'
import os
import sys

import torch
import torch.distributed as dist
import torch.multiprocessing as mp


def _worker(rank, world_size, results_path, port):
    os.environ['MASTER_ADDR'] = '127.0.0.1'
    os.environ['MASTER_PORT'] = str(port)
    try:
        dist.init_process_group('nccl', rank=rank, world_size=world_size)
        torch.cuda.set_device(rank)

        # all_reduce: each GPU contributes rank+1, result should be sum of 1..N
        t = torch.tensor([rank + 1], dtype=torch.float32, device=f'cuda:{rank}')
        dist.all_reduce(t, op=dist.ReduceOp.SUM)
        expected = world_size * (world_size + 1) / 2
        assert t.item() == expected, f'rank {rank}: all_reduce expected {expected}, got {t.item()}'

        # broadcast: rank 0 sends, all others receive
        b = torch.tensor([42.0], device=f'cuda:{rank}') if rank == 0 else torch.zeros(1, device=f'cuda:{rank}')
        dist.broadcast(b, src=0)
        assert b.item() == 42.0, f'rank {rank}: broadcast expected 42, got {b.item()}'

        # all_gather: collect rank IDs from every GPU
        gather_list = [torch.zeros(1, device=f'cuda:{rank}') for _ in range(world_size)]
        dist.all_gather(gather_list, torch.tensor([float(rank)], device=f'cuda:{rank}'))
        gathered = sorted(int(g.item()) for g in gather_list)
        assert gathered == list(range(world_size)), f'rank {rank}: all_gather got {gathered}'

        dist.destroy_process_group()

        with open(f'{results_path}.{rank}', 'w') as f:
            f.write('ok')
    except Exception as e:
        with open(f'{results_path}.{rank}', 'w') as f:
            f.write(str(e))


if __name__ == '__main__':
    world = torch.cuda.device_count()
    port = int(sys.argv[1])
    results_path = '/tmp/nccl_test_result'

    for i in range(world):
        try:
            os.remove(f'{results_path}.{i}')
        except FileNotFoundError:
            pass

    mp.spawn(_worker, args=(world, results_path, port), nprocs=world, join=True)

    failures = []
    for i in range(world):
        try:
            with open(f'{results_path}.{i}') as f:
                result = f.read().strip()
            if result != 'ok':
                failures.append(f'rank {i}: {result}')
        except FileNotFoundError:
            failures.append(f'rank {i}: no result file')

    if failures:
        for f in failures:
            print(f'  FAIL: {f}')
        sys.exit(1)

    print(f'  NCCL all_reduce: ok ({world} GPUs)')
    print('  NCCL broadcast: ok')
    print('  NCCL all_gather: ok')
NCCL_EOF

"${NCCL_VENV}bin/python3" "$NCCL_SCRIPT" "$MASTER_PORT" 2>&1 \
    || fail_later "nccl" "multi-GPU NCCL communication failed"
rm -f "$NCCL_SCRIPT"

report_failures
test_pass "NCCL collectives verified across ${_device_count} GPUs"
