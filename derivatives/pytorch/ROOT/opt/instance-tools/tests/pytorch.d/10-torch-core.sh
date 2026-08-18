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

    # Import and version info (stderr kept separate to avoid CUDA/NVML warnings corrupting JSON)
    local torch_info torch_stderr
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
" 2>/tmp/torch_info_stderr.txt) || {
        torch_stderr=$(cat /tmp/torch_info_stderr.txt 2>/dev/null)
        fail_later "${venv_name}-import" "cannot import torch in ${venv_dir}: ${torch_stderr}"
        return
    }
    # Log any stderr warnings for debugging
    if [[ -s /tmp/torch_info_stderr.txt ]]; then
        echo "  ${label} warnings during torch import:"
        sed 's/^/    /' /tmp/torch_info_stderr.txt
    fi

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
    print(f'  GPU {i}: {props.name} ({props.total_memory / 1e9:.1f} GB)')
" 2>&1 || fail_later "${venv_name}-gpu-info" "failed to query GPU properties"
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

# ── Multi-GPU communication (NCCL) ────────────────────────────────────
#
# MOVED OUT (ADR 0021 decision 5). The collectives harness used to live here,
# behind `device_count > 1`, falling through on a single GPU to a bare
# `echo "  skip: multi-GPU comms (single GPU)"` — which left THIS test passing
# either way. The required-pass gate matches on test states and cannot see a
# branch inside a passing test, so the collectives were unreachable by any gate.
#
# They are now two sibling tests that can each be named and can each fail:
#   40-nccl-init.sh        NCCL usable from torch.distributed (world_size=1) —
#                          the image-owned surface; runs on every GPU box.
#   41-nccl-collectives.sh the multi-GPU matrix; test_skips below 2 GPUs.

report_failures
test_pass "torch verified across ${#TORCH_VENVS[@]} venv(s) (CUDA: ${_first_cuda_version}, devices: ${_device_count})"
