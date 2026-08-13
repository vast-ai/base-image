#!/bin/bash
# Test: GPU-adjacent libraries — OpenCL, Vulkan, Infiniband/RDMA.
# Verifies that libraries installed by the Dockerfile are loadable and tools work.
source "$(dirname "$0")/../lib.sh"

has_gpu || test_skip "no GPU detected"

# FAILURES and fail_later/report_failures come from lib.sh
#
# THE RULE THIS FILE FOLLOWS (ADR 0019, enforced by L059): absent is fine,
# INSTALLED-BUT-BROKEN is a failure.
#
# Until 2026-08-07 every check here was an `echo WARN` and the file contained no
# fail_later call at all, so it reported `passed` on every box while being named
# in base-qa's INSTANCE_TEST_REQUIRE_PASS — i.e. a required test that could not
# fail, asserting nothing beyond `has_gpu`. The distinction below is what makes
# it assertable: whether a library is SHIPPED is a property of the image (ours to
# get right), whether the hardware exists is a property of the host (not ours,
# and not something a QA cell should block on).

# Is the dynamic linker aware of this library? Distinguishes "we never shipped
# it" (fine) from "we shipped it and it will not load" (a real defect). CDLL
# alone cannot tell those apart — it fails identically for both.
lib_installed() {
    ldconfig -p 2>/dev/null | grep -qF "$1"
}

lib_loads() {
    python3 -c "import ctypes,sys; ctypes.CDLL(sys.argv[1])" "$1" 2>/dev/null
}

# Shipped => must load. Not shipped => nothing to assert.
check_lib() {
    local lib="$1" what="$2" name="$3"
    if lib_loads "$lib"; then
        echo "  ${lib}: loadable"
        return 0
    fi
    if lib_installed "$lib"; then
        # fail_later is <name> <message>: the name is what the summary line and
        # the QA verdict carry, so it has to stay short and greppable.
        fail_later "$name" \
            "${lib} is installed but will not load — broken ${what} stack"
        return 1
    fi
    echo "  absent (ok): ${lib}"
    return 0
}

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

check_lib libOpenCL.so.1 OpenCL "opencl-icd"

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

# check_lib returns 0 for "absent" as well as "loadable" — that is right for the
# verdict and wrong for a tally, so count what actually loaded rather than what
# did not fail. Reporting "3/3 loadable" for three absent libraries is the same
# class of lie as a passing test that says it verified something.
ib_ok=0
for lib in "${ib_libs[@]}"; do
    # check_lib already probed it; reuse that instead of a second interpreter start.
    if lib_loads "$lib"; then _loaded=true; else _loaded=false; fi
    check_lib "$lib" RDMA "rdma-${lib%%.*}"
    [[ "$_loaded" == true ]] && ib_ok=$((ib_ok + 1))
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

# Two sonames because the base ships the versioned one and some CUDA layouts only
# carry the unversioned symlink. Either loading is enough; only a shipped-but-
# broken NCCL is a defect, and it is one that matters — torch.distributed dlopens
# this at init, so a broken NCCL is invisible until a collective is attempted.
if lib_loads libnccl.so.2; then
    echo "  libnccl.so.2: loadable"
elif lib_loads libnccl.so; then
    echo "  libnccl.so: loadable"
elif lib_installed libnccl.so; then
    fail_later "nccl" \
        "libnccl is installed but will not load — torch.distributed would fail at init"
else
    echo "  absent (ok): libnccl"
fi

# ── Libraries the image installs unconditionally MUST be present ──────
#
# "absent is fine" is right for hardware-conditional things and wrong for
# packages we always install: without this, an image that dropped them passes a
# REQUIRED test, because check_lib returns 0 for absent.
#
# Each assertion is keyed on WHO INSTALLS IT, not on a blanket image-class check.
# That distinction matters because external images run this same suite — they
# copy the whole ROOT overlay from the build context — while being built by
# tools/convert-non-vast-image.sh rather than by our Dockerfile, and the two
# install different sets.
#
#   libibverbs / librdmacm : Dockerfile:127,130 AND convert-non-vast-image.sh:54,57
#                            -> every image carrying this test has them.
#   libOpenCL              : Dockerfile:180 only -> vast images only.
#   libibumad              : same apt block as the RDMA pair in both installers.
#   libnccl                : ONLY where the package is actually installed. The
#                            CUDA toolkit is not a proxy for it: Dockerfile.runtime
#                            installs libnccl2 for the MINI variants, while the 11
#                            non-mini configs build from nvidia/cuda:*-cudnn-devel
#                            and inherit NCCL from upstream — which CUDA 13 does
#                            not ship. Measured: nvidia/cuda:13.2.1 has no
#                            libnccl2 package at all, so keying this on
#                            /usr/local/cuda-*/ would have failed a REQUIRED test
#                            on every 13.x config and held four -auto tags.
for _req in libibverbs.so.1 librdmacm.so.1 libibumad.so.3; do
    lib_installed "$_req" \
        || fail_later "missing-${_req%%.*}" \
            "${_req} is not installed — every image shipping this test installs it, so it \
was dropped from the image rather than being unavailable on this host"
done

if is_vast_image; then
    lib_installed libOpenCL.so.1 \
        || fail_later "missing-libOpenCL" \
            "libOpenCL.so.1 is not installed — the base image installs ocl-icd-libopencl1 \
unconditionally"
    # "We installed the package, so the soname must be resolvable" — decidable,
    # and true on every variant. An image that never shipped NCCL is not thereby
    # broken; one that installed it and cannot load it is.
    if dpkg-query -W libnccl2 >/dev/null 2>&1; then
        lib_installed libnccl.so.2 \
            || fail_later "missing-libnccl" \
                "libnccl2 is installed but libnccl.so.2 is not on the loader path — \
multi-GPU and torch.distributed would fail at init"
    fi
fi

# ── Report ────────────────────────────────────────────────────────────

report_failures

test_pass "GPU libraries verified (OpenCL, Vulkan, RDMA, NCCL)"
