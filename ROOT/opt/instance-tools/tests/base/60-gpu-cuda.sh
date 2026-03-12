#!/bin/bash
# Test: GPU and CUDA availability.
source "$(dirname "$0")/../lib.sh"

has_gpu || test_skip "no GPU detected"

# nvidia-smi runs
nvidia-smi &>/dev/null || test_fail "nvidia-smi failed"
echo "  $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -1)"

# /usr/local/cuda is a symlink to a real cuda-X.Y directory
if [[ -L /usr/local/cuda ]]; then
    cuda_target=$(readlink -f /usr/local/cuda)
    [[ -d "$cuda_target" ]] || test_fail "/usr/local/cuda symlink target does not exist: $cuda_target"
    echo "  cuda: $cuda_target"
else
    echo "  WARN: /usr/local/cuda is not a symlink"
fi

# nvcc exists
if command -v nvcc &>/dev/null; then
    echo "  nvcc: $(nvcc --version 2>&1 | tail -1)"
else
    echo "  WARN: nvcc not found in PATH"
fi

# libcudart in linker cache
if ldconfig -p 2>/dev/null | grep -q libcudart; then
    echo "  libcudart found in ldconfig"
else
    echo "  WARN: libcudart not in ldconfig cache"
fi

# OpenCL ICD
if [[ -f /etc/OpenCL/vendors/nvidia.icd ]]; then
    grep -q "libnvidia-opencl" /etc/OpenCL/vendors/nvidia.icd \
        || echo "  WARN: nvidia.icd does not reference libnvidia-opencl"
    echo "  OpenCL ICD present"
else
    echo "  absent (ok): OpenCL ICD"
fi

# Python can load libcuda
python3 -c "import ctypes; ctypes.CDLL('libcuda.so.1')" 2>/dev/null \
    && echo "  python ctypes: libcuda.so.1 loadable" \
    || echo "  WARN: python cannot load libcuda.so.1"

test_pass "GPU and CUDA verified"
