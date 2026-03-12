#!/bin/bash
# Test: GPU, CUDA toolkit selection, and forward compatibility.
source "$(dirname "$0")/../lib.sh"

has_gpu || test_skip "no GPU detected"

# ── Basic GPU info ────────────────────────────────────────────────────

nvidia-smi &>/dev/null || test_fail "nvidia-smi failed"

gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
compute_cap=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1)
driver_ver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
driver_cuda=$(nvidia-smi 2>/dev/null | grep -oP "CUDA Version: \K[0-9]+\.[0-9]+")

echo "  GPU: ${gpu_name} (CC ${compute_cap}, Driver ${driver_ver}, Max CUDA ${driver_cuda})"

# ── CUDA toolkit selection ────────────────────────────────────────────

# Collect installed CUDA versions (same logic as 05-configure-cuda.sh)
cuda_versions=()
for dir in /usr/local/cuda-*/; do
    [[ -d "$dir" ]] || continue
    ver=$(basename "$dir" | sed 's/cuda-//')
    [[ "$ver" =~ ^[0-9]+\.[0-9]+$ ]] && cuda_versions+=("$ver")
done

if [[ ${#cuda_versions[@]} -eq 0 ]]; then
    echo "  no CUDA toolkits installed (skip toolkit/compat checks)"
    # Still check basic driver-level functionality
    python3 -c "import ctypes; ctypes.CDLL('libcuda.so.1')" 2>/dev/null \
        && echo "  python ctypes: libcuda.so.1 loadable" \
        || echo "  WARN: python cannot load libcuda.so.1"
    test_pass "GPU present, no CUDA toolkit installed"
fi

readarray -t cuda_versions < <(printf '%s\n' "${cuda_versions[@]}" | sort -t. -k1,1nr -k2,2nr)
latest_cuda="${cuda_versions[0]}"
echo "  installed CUDA: ${cuda_versions[*]} (latest: ${latest_cuda})"

# /usr/local/cuda must be a symlink (created by 05-configure-cuda.sh)
if [[ -L /usr/local/cuda ]]; then
    selected_cuda=$(readlink /usr/local/cuda | sed 's|.*/cuda-||')
    cuda_target=$(readlink -f /usr/local/cuda)
    [[ -d "$cuda_target" ]] || test_fail "/usr/local/cuda symlink target does not exist: $cuda_target"
    echo "  selected CUDA: ${selected_cuda} (symlink → ${cuda_target})"
else
    test_fail "/usr/local/cuda is not a symlink (05-configure-cuda.sh should have created it)"
fi

# ── Forward compatibility validation ──────────────────────────────────

# Forward compat is enabled when:
#   1. The latest installed CUDA version > driver's max CUDA
#   2. The compat libs in cuda-X.Y/compat/ load successfully (datacenter GPUs)
#   3. /etc/ld.so.conf.d/0-compat-cuda.conf exists pointing to compat dir
#
# Forward compat is NOT expected when:
#   - Driver already supports the latest toolkit (latest_cuda <= driver_cuda)
#   - No compat libs exist for the latest toolkit
#   - Consumer GPU that doesn't support forward compat (cuInit fails)
#   - DISABLE_FORWARD_COMPAT=true

compat_conf="/etc/ld.so.conf.d/0-compat-cuda.conf"
compat_dir="/usr/local/cuda-${latest_cuda}/compat"
toolkit_needs_compat=$(awk "BEGIN {print ($latest_cuda > $driver_cuda) ? 1 : 0}" 2>/dev/null || echo "0")

if [[ "$toolkit_needs_compat" == "1" ]]; then
    echo "  latest CUDA ${latest_cuda} > driver max ${driver_cuda}: forward compat needed"

    if [[ "${DISABLE_FORWARD_COMPAT:-false}" == "true" ]]; then
        echo "  DISABLE_FORWARD_COMPAT=true — forward compat intentionally disabled"
        # Selected CUDA should be <= driver max
        if awk "BEGIN {exit !($selected_cuda <= $driver_cuda)}"; then
            echo "  selected CUDA ${selected_cuda} <= driver max ${driver_cuda}: correct fallback"
        else
            test_fail "forward compat disabled but selected CUDA ${selected_cuda} > driver max ${driver_cuda}"
        fi
    elif [[ -d "$compat_dir" ]] && compgen -G "$compat_dir/libcuda.so.*" > /dev/null; then
        # Compat libs exist — check if they were activated
        if [[ -f "$compat_conf" ]]; then
            compat_path=$(cat "$compat_conf")
            echo "  forward compat ENABLED: ${compat_path}"
            # Selected CUDA should be the latest (compat allows it)
            [[ "$selected_cuda" == "$latest_cuda" ]] \
                || echo "  WARN: compat enabled but selected CUDA ${selected_cuda} != latest ${latest_cuda}"
            # Verify compat libs are in the linker cache
            if ldconfig -p 2>/dev/null | grep -q "$compat_dir"; then
                echo "  compat libs in ldconfig cache"
            else
                test_fail "compat conf exists but libs not in ldconfig cache (ldconfig may not have run)"
            fi
            # Verify cuInit works through compat
            if LD_LIBRARY_PATH="$compat_dir" python3 -c "
import ctypes, sys
sys.exit(0 if ctypes.CDLL('libcuda.so.1').cuInit(0) == 0 else 1)
" 2>/dev/null; then
                echo "  cuInit succeeds via compat libs"
            else
                test_fail "forward compat enabled but cuInit fails"
            fi
        else
            # Compat libs exist but weren't activated — cuInit likely failed (consumer GPU)
            echo "  compat libs present at ${compat_dir} but not activated"
            echo "  (consumer GPU or cuInit failed — expected on non-datacenter hardware)"
            # Should have fallen back to compatible version
            if awk "BEGIN {exit !($selected_cuda <= $driver_cuda)}"; then
                echo "  selected CUDA ${selected_cuda} <= driver max ${driver_cuda}: correct fallback"
            else
                test_fail "compat not activated but selected CUDA ${selected_cuda} > driver max ${driver_cuda}"
            fi
        fi
    else
        # No compat libs at all
        echo "  no compat libs in ${compat_dir}"
        if awk "BEGIN {exit !($selected_cuda <= $driver_cuda)}"; then
            echo "  selected CUDA ${selected_cuda} <= driver max ${driver_cuda}: correct fallback"
        else
            test_fail "no compat libs and selected CUDA ${selected_cuda} > driver max ${driver_cuda}"
        fi
    fi
else
    echo "  latest CUDA ${latest_cuda} <= driver max ${driver_cuda}: no forward compat needed"
    if [[ -f "$compat_conf" ]]; then
        echo "  WARN: forward compat conf exists but shouldn't be needed"
    fi
fi

# ── Standard CUDA checks ─────────────────────────────────────────────

# nvcc available
if command -v nvcc &>/dev/null; then
    echo "  nvcc: $(nvcc --version 2>&1 | grep -oP 'release \K[0-9.]+' || nvcc --version 2>&1 | tail -1)"
else
    echo "  WARN: nvcc not found in PATH"
fi

# CUDA libs in ldconfig
if [[ -f /etc/ld.so.conf.d/10-cuda.conf ]]; then
    echo "  10-cuda.conf: $(cat /etc/ld.so.conf.d/10-cuda.conf)"
else
    echo "  WARN: /etc/ld.so.conf.d/10-cuda.conf not present"
fi

if ldconfig -p 2>/dev/null | grep -q libcudart; then
    echo "  libcudart found in ldconfig"
else
    echo "  WARN: libcudart not in ldconfig cache"
fi

# Python can load libcuda
python3 -c "import ctypes; ctypes.CDLL('libcuda.so.1')" 2>/dev/null \
    && echo "  python ctypes: libcuda.so.1 loadable" \
    || echo "  WARN: python cannot load libcuda.so.1"

# OpenCL ICD
if [[ -f /etc/OpenCL/vendors/nvidia.icd ]]; then
    echo "  OpenCL ICD present"
else
    echo "  absent (ok): OpenCL ICD"
fi

test_pass "GPU and CUDA verified (selected: ${selected_cuda}, compat: ${toolkit_needs_compat})"
