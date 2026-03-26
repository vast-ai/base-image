#!/bin/bash
# Test: PyTorch core — import, CUDA availability, tensor operations on GPU.
# Iterates over all venvs in /venv/ that have torch installed.
source "$(dirname "$0")/../lib.sh"

# ── Discover torch venvs ──────────────────────────────────────────────

TORCH_VENVS=()
for venv_dir in /venv/*/; do
    [[ -f "${venv_dir}bin/activate" ]] || continue
    # Check if torch is installed without activating
    if "${venv_dir}bin/python3" -c "import torch" 2>/dev/null; then
        TORCH_VENVS+=("$venv_dir")
    fi
done

[[ ${#TORCH_VENVS[@]} -gt 0 ]] || test_skip "no venvs with torch found in /venv/"
echo "  found ${#TORCH_VENVS[@]} venv(s) with torch: ${TORCH_VENVS[*]}"

# Track GPU state across venvs (for one-time checks after the loop)
_cuda_available="False"
_device_count=0
_first_torch_version=""
_first_cuda_version="N/A"

# ── Per-venv validation ───────────────────────────────────────────────

test_venv() {
    local venv_dir="$1"
    local venv_name
    venv_name=$(basename "$venv_dir")
    local py="${venv_dir}bin/python3"
    local label="[${venv_name}]"

    echo ""
    echo "  ${label} ── venv: ${venv_dir}"

    # Import and version info
    local torch_info
    torch_info=$("$py" -c "
import torch, json, sys
info = {
    'version': torch.__version__,
    'cuda_built': torch.backends.cuda.is_built(),
    'cuda_available': torch.cuda.is_available(),
    'cuda_version': getattr(torch.version, 'cuda', None),
    'cudnn_enabled': torch.backends.cudnn.enabled if torch.cuda.is_available() else False,
    'cudnn_version': torch.backends.cudnn.version() if torch.cuda.is_available() and torch.backends.cudnn.enabled else None,
    'device_count': torch.cuda.device_count() if torch.cuda.is_available() else 0,
}
json.dump(info, sys.stdout)
" 2>&1) || { fail_later "${venv_name}-import" "cannot import torch in ${venv_dir}: ${torch_info}"; return; }

    local torch_version cuda_available cuda_version cudnn_version device_count cuda_built
    torch_version=$(echo "$torch_info" | "$py" -c "import sys,json; print(json.load(sys.stdin)['version'])")
    cuda_available=$(echo "$torch_info" | "$py" -c "import sys,json; print(json.load(sys.stdin)['cuda_available'])")
    cuda_version=$(echo "$torch_info" | "$py" -c "import sys,json; print(json.load(sys.stdin)['cuda_version'] or 'N/A')")
    cudnn_version=$(echo "$torch_info" | "$py" -c "import sys,json; v=json.load(sys.stdin)['cudnn_version']; print(v if v else 'N/A')")
    device_count=$(echo "$torch_info" | "$py" -c "import sys,json; print(json.load(sys.stdin)['device_count'])")
    cuda_built=$(echo "$torch_info" | "$py" -c "import sys,json; print(json.load(sys.stdin)['cuda_built'])")

    echo "  ${label} torch: ${torch_version}"
    echo "  ${label} CUDA built: ${cuda_built}"
    echo "  ${label} CUDA available: ${cuda_available}, version: ${cuda_version}"
    echo "  ${label} cuDNN: ${cudnn_version}"
    echo "  ${label} devices: ${device_count}"

    # Export for post-loop one-time checks
    if [[ -z "$_first_torch_version" ]]; then
        _first_torch_version="$torch_version"
        _first_cuda_version="$cuda_version"
    fi
    [[ "$cuda_available" == "True" ]] && _cuda_available="True"
    [[ "$device_count" -gt "$_device_count" ]] && _device_count="$device_count"

    # Verify PYTORCH_VERSION env matches (only for the main venv)
    if [[ "$venv_name" == "main" && -n "${PYTORCH_VERSION:-}" ]]; then
        if [[ "$torch_version" == "${PYTORCH_VERSION}"* ]]; then
            echo "  ${label} PYTORCH_VERSION=${PYTORCH_VERSION} matches installed"
        else
            fail_later "${venv_name}-version" "PYTORCH_VERSION=${PYTORCH_VERSION} but installed is ${torch_version}"
        fi
    fi

    # If CUDA image, torch must be built with CUDA
    if [[ -d /usr/local/cuda && "$cuda_built" != "True" ]]; then
        fail_later "${venv_name}-cuda-built" "CUDA toolkit present but torch not built with CUDA support"
    fi

    # GPU info (first venv only — hardware doesn't change)
    if has_gpu && [[ "$cuda_available" == "True" && -z "$_gpu_info_printed" ]]; then
        "$py" -c "
import torch
for i in range(torch.cuda.device_count()):
    props = torch.cuda.get_device_properties(i)
    print(f'  GPU {i}: {props.name} ({props.total_mem / 1e9:.1f} GB)')
"
        _gpu_info_printed=1
    fi

    # Tensor operations on GPU
    if has_gpu && [[ "$cuda_available" == "True" ]]; then
        "$py" -c "
import torch
a = torch.randn(1024, 1024, device='cuda')
b = torch.randn(1024, 1024, device='cuda')
c = torch.matmul(a, b)
torch.cuda.synchronize()
assert c.shape == (1024, 1024), f'unexpected shape {c.shape}'
print('  ${label} matmul 1024x1024: ok')
" 2>&1 || fail_later "${venv_name}-matmul" "GPU matmul failed"

        "$py" -c "
import torch
x = torch.randn(64, 3, 224, 224, device='cuda')
conv = torch.nn.Conv2d(3, 16, 3, padding=1).cuda()
y = conv(x)
torch.cuda.synchronize()
assert y.shape == (64, 16, 224, 224), f'unexpected shape {y.shape}'
print('  ${label} Conv2d forward: ok')
" 2>&1 || fail_later "${venv_name}-conv2d" "GPU Conv2d forward failed"

        "$py" -c "
import torch
t_cpu = torch.randn(256, 256)
t_gpu = t_cpu.to('cuda')
t_back = t_gpu.to('cpu')
assert torch.allclose(t_cpu, t_back), 'CPU/GPU round-trip mismatch'
print('  ${label} CPU <-> GPU transfer: ok')
" 2>&1 || fail_later "${venv_name}-transfer" "CPU/GPU transfer failed"

        "$py" -c "
import torch
alloc = torch.cuda.memory_allocated() / 1e6
reserved = torch.cuda.memory_reserved() / 1e6
print(f'  ${label} memory: {alloc:.1f} MB allocated / {reserved:.1f} MB reserved')
"
    else
        echo "  ${label} skip: GPU tensor tests (no CUDA)"
    fi
}

_gpu_info_printed=""
for venv_dir in "${TORCH_VENVS[@]}"; do
    test_venv "$venv_dir"
done

# ── Multi-GPU communication (NCCL) — once, not per-venv ───────────────

echo ""
if has_gpu && [[ "$_cuda_available" == "True" ]] && [[ "$_device_count" -gt 1 ]]; then
    # Use the first torch venv for the NCCL test
    NCCL_PY="${TORCH_VENVS[0]}bin/python3"
    "$NCCL_PY" -c "
import torch
import torch.distributed as dist
import torch.multiprocessing as mp
import sys, os

def _worker(rank, world_size, results_path):
    '''Run in a subprocess — one per GPU.'''
    os.environ['MASTER_ADDR'] = '127.0.0.1'
    os.environ['MASTER_PORT'] = '29500'
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

world = torch.cuda.device_count()
results_path = '/tmp/nccl_test_result'

for i in range(world):
    try:
        os.remove(f'{results_path}.{i}')
    except FileNotFoundError:
        pass

mp.spawn(_worker, args=(world, results_path), nprocs=world, join=True)

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
print(f'  NCCL broadcast: ok')
print(f'  NCCL all_gather: ok')
" 2>&1 || fail_later "nccl" "multi-GPU NCCL communication failed"
elif has_gpu && [[ "$_cuda_available" == "True" ]]; then
    echo "  skip: multi-GPU comms (single GPU)"
else
    echo "  skip: multi-GPU comms (no CUDA)"
fi

report_failures
test_pass "torch verified across ${#TORCH_VENVS[@]} venv(s) (CUDA: ${_first_cuda_version}, devices: ${_device_count})"
