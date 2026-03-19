#!/bin/bash
# Test: CUDA compute operations via driver API (no torch/framework dependency).
# Verifies cuInit, context creation, memory alloc/free on each GPU.
# If multiple GPUs, tests peer-to-peer access between all pairs.
source "$(dirname "$0")/../lib.sh"

has_gpu || test_skip "no GPU detected"

# All CUDA operations use the driver API via Python ctypes — no pip packages needed.
# Python exits 77 to signal skip (e.g. libcuda not loadable), 1 for failure.
set +e
python3 << 'CUDA_TEST'
import ctypes
import ctypes.util
import sys

# ── Load CUDA driver library ─────────────────────────────────────────

def load_cuda():
    for name in ("libcuda.so.1", "libcuda.so"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    path = ctypes.util.find_library("cuda")
    if path:
        return ctypes.CDLL(path)
    return None

cuda = load_cuda()
if cuda is None:
    print("  SKIP: libcuda.so not loadable", flush=True)
    sys.exit(77)

# ── CUDA driver API helpers ───────────────────────────────────────────

CU_CTX_SCHED_AUTO = 0

def check(fn_name, ret):
    if ret != 0:
        err_name = ctypes.c_char_p()
        cuda.cuGetErrorName(ret, ctypes.byref(err_name))
        name = err_name.value.decode() if err_name.value else str(ret)
        print(f"  FAIL: {fn_name} returned {name} ({ret})", flush=True)
        sys.exit(1)

# ── cuInit ────────────────────────────────────────────────────────────

check("cuInit", cuda.cuInit(0))
print("  cuInit(0): ok", flush=True)

# ── Device count ──────────────────────────────────────────────────────

count = ctypes.c_int(0)
check("cuDeviceGetCount", cuda.cuDeviceGetCount(ctypes.byref(count)))
num_gpus = count.value
print(f"  devices: {num_gpus}", flush=True)

if num_gpus == 0:
    print("  SKIP: cuDeviceGetCount returned 0", flush=True)
    sys.exit(77)

# ── Per-GPU: context + memory alloc/free ──────────────────────────────

devices = []
contexts = []

for i in range(num_gpus):
    dev = ctypes.c_int(0)
    check(f"cuDeviceGet[{i}]", cuda.cuDeviceGet(ctypes.byref(dev), i))
    devices.append(dev)

    # Device name
    name_buf = ctypes.create_string_buffer(256)
    check(f"cuDeviceGetName[{i}]", cuda.cuDeviceGetName(name_buf, 256, dev))
    gpu_name = name_buf.value.decode()

    # Total memory
    mem = ctypes.c_size_t(0)
    cuda.cuDeviceTotalMem_v2(ctypes.byref(mem), dev)
    mem_gb = mem.value / (1024**3)

    # Create context
    ctx = ctypes.c_void_p(0)
    check(f"cuCtxCreate[{i}]", cuda.cuCtxCreate_v2(ctypes.byref(ctx), CU_CTX_SCHED_AUTO, dev))
    contexts.append(ctx)

    # Allocate 32 MB of device memory
    dptr = ctypes.c_uint64(0)
    alloc_size = 32 * 1024 * 1024
    check(f"cuMemAlloc[{i}]", cuda.cuMemAlloc_v2(ctypes.byref(dptr), alloc_size))

    # Free it
    check(f"cuMemFree[{i}]", cuda.cuMemFree_v2(dptr))

    print(f"  GPU {i}: {gpu_name} ({mem_gb:.1f} GB) — ctx + alloc/free ok", flush=True)

    # Pop context (leave no active context)
    cuda.cuCtxPopCurrent_v2(ctypes.byref(ctx))

# ── Multi-GPU: peer access test ───────────────────────────────────────

if num_gpus > 1:
    print(f"  -- peer access ({num_gpus} GPUs) --", flush=True)
    peer_ok = 0
    peer_fail = 0
    peer_na = 0

    for i in range(num_gpus):
        for j in range(num_gpus):
            if i == j:
                continue

            can_access = ctypes.c_int(0)
            ret = cuda.cuDeviceCanAccessPeer(ctypes.byref(can_access), devices[i], devices[j])
            if ret != 0:
                print(f"  GPU {i} → {j}: cuDeviceCanAccessPeer error ({ret})", flush=True)
                peer_fail += 1
                continue

            if can_access.value == 0:
                print(f"  GPU {i} → {j}: no peer access (topology)", flush=True)
                peer_na += 1
                continue

            # Enable peer access: push ctx[i], enable access to dev[j]
            cuda.cuCtxPushCurrent_v2(contexts[i])
            ret = cuda.cuCtxEnablePeerAccess(contexts[j], 0)
            cuda.cuCtxPopCurrent_v2(ctypes.byref(ctypes.c_void_p()))

            if ret == 0:
                print(f"  GPU {i} → {j}: peer access enabled", flush=True)
                peer_ok += 1
            elif ret == 704:
                # CUDA_ERROR_PEER_ACCESS_ALREADY_ENABLED
                print(f"  GPU {i} → {j}: peer access already enabled", flush=True)
                peer_ok += 1
            else:
                err_name = ctypes.c_char_p()
                cuda.cuGetErrorName(ret, ctypes.byref(err_name))
                name = err_name.value.decode() if err_name.value else str(ret)
                print(f"  GPU {i} → {j}: cuCtxEnablePeerAccess failed ({name})", flush=True)
                peer_fail += 1

    print(f"  peer results: {peer_ok} ok, {peer_na} unavailable, {peer_fail} failed", flush=True)
    if peer_fail > 0:
        print(f"  WARN: {peer_fail} peer access failure(s)", flush=True)

# ── Cleanup contexts ──────────────────────────────────────────────────

for ctx in contexts:
    cuda.cuCtxDestroy_v2(ctx)

print("  all contexts destroyed", flush=True)
CUDA_TEST
cuda_rc=$?
set -e

if [[ $cuda_rc -eq 77 ]]; then
    test_skip "CUDA driver not loadable"
elif [[ $cuda_rc -ne 0 ]]; then
    test_fail "CUDA compute test failed (exit code ${cuda_rc})"
fi

gpu_count=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l)
if [[ "$gpu_count" -gt 1 ]]; then
    test_pass "CUDA compute verified on ${gpu_count} GPUs with peer access test"
else
    test_pass "CUDA compute verified (single GPU)"
fi
