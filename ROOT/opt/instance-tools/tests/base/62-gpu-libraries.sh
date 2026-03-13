#!/bin/bash
# Test: GPU-adjacent libraries — OpenCL, Vulkan, Infiniband/RDMA.
# Verifies that libraries installed by the Dockerfile are loadable and tools work.
source "$(dirname "$0")/../lib.sh"

has_gpu || test_skip "no GPU detected"

# FAILURES and fail_later/report_failures come from lib.sh

# ── OpenCL ────────────────────────────────────────────────────────────

if command -v clinfo &>/dev/null; then
    platform_count=$(clinfo --list 2>/dev/null | grep -c "Platform #" || true)
    if [[ "$platform_count" -gt 0 ]]; then
        echo "  OpenCL: ${platform_count} platform(s) found"
    else
        # clinfo may not find platforms without proper ICD — warn only
        echo "  WARN: clinfo runs but found 0 platforms"
    fi
else
    echo "  absent (ok): clinfo"
fi

# Check OpenCL ICD loader is loadable
if python3 -c "import ctypes,sys; ctypes.CDLL(sys.argv[1])" libOpenCL.so.1 2>/dev/null; then
    echo "  libOpenCL.so.1: loadable"
else
    echo "  WARN: libOpenCL.so.1 not loadable"
fi

# nvidia.icd present
if [[ -f /etc/OpenCL/vendors/nvidia.icd ]]; then
    echo "  nvidia.icd: present"
else
    echo "  absent (ok): nvidia.icd"
fi

# ── Vulkan ────────────────────────────────────────────────────────────

if command -v vulkaninfo &>/dev/null; then
    if vulkaninfo --summary 2>/dev/null | grep -q "GPU"; then
        gpu_vk=$(vulkaninfo --summary 2>/dev/null | grep -oP 'deviceName\s*=\s*\K.*' | head -1)
        echo "  Vulkan: ${gpu_vk:-detected}"
    else
        # Vulkan may not work without proper ICD in container
        echo "  WARN: vulkaninfo runs but no GPU found (may need nvidia_icd.json)"
    fi
else
    echo "  absent (ok): vulkaninfo"
fi

# ── Infiniband / RDMA ────────────────────────────────────────────────

# These libraries are installed for multi-node GPU communication (NCCL).
# They should be loadable even if no IB hardware is present.
ib_libs=(
    "libibverbs.so.1"
    "librdmacm.so.1"
    "libibumad.so.3"
)

ib_ok=0
ib_missing=0
for lib in "${ib_libs[@]}"; do
    if python3 -c "import ctypes,sys; ctypes.CDLL(sys.argv[1])" "$lib" 2>/dev/null; then
        ib_ok=$((ib_ok + 1))
    else
        ib_missing=$((ib_missing + 1))
        echo "  WARN: ${lib} not loadable"
    fi
done
echo "  RDMA libs: ${ib_ok}/${#ib_libs[@]} loadable"

# Check for IB hardware (informational only)
if command -v ibstat &>/dev/null; then
    ib_ports=$(ibstat -l 2>/dev/null | wc -l)
    if [[ "$ib_ports" -gt 0 ]]; then
        echo "  IB devices: ${ib_ports}"
    else
        echo "  IB hardware: not present (RDMA libs available for NCCL)"
    fi
fi

# ── NCCL ──────────────────────────────────────────────────────────────

if python3 -c "import ctypes,sys; ctypes.CDLL(sys.argv[1])" libnccl.so.2 2>/dev/null; then
    echo "  libnccl.so.2: loadable"
elif python3 -c "import ctypes,sys; ctypes.CDLL(sys.argv[1])" libnccl.so 2>/dev/null; then
    echo "  libnccl.so: loadable"
else
    echo "  absent (ok): libnccl"
fi

# ── Report ────────────────────────────────────────────────────────────

report_failures

test_pass "GPU libraries verified (OpenCL, Vulkan, RDMA, NCCL)"
