#!/bin/bash
# Test: the llama.cpp CUDA backend shipped with the studio is USABLE on this host.
#
# WHY THIS EXISTS AT RUNTIME AND NOT ONLY AT BUILD TIME. llama.cpp is built
# GGML_BACKEND_DL=ON, so the CUDA backend is a dlopen'd plugin. When that dlopen fails
# the binary does not crash and does not exit non-zero — it falls back to CPU and serves
# correct answers, slowly, forever. The build asserts the file exists, resolves, and
# carries SASS for this image's GPU floor (L056/L081), but every one of those can hold
# while the backend still fails to load HERE: a libcublas minor the bundle was not built
# against, a driver too old for its CUDA major, or a host GPU outside the SASS set.
# That is ADR 0016's defect moved from build time to runtime, and only a live GPU sees it.
#
# SCOPE, STATED HONESTLY. The llama-cpp image's equivalent has a second arm that asks the
# DRIVER how much VRAM the running llama-server holds. That arm cannot exist here: the
# studio does not run llama-server as a service — it invokes it for a job — so there is
# no long-lived pid to attribute memory to. This file therefore proves the backend LOADS
# on this host, not that a given training run offloaded. The `-ngl 0` / partial-offload
# family is out of reach without starting a server and a model, which this smoke
# deliberately does not do.
# TEST_TIMEOUT=600
source "$(dirname "$0")/../lib.sh"

LLAMA_BIN="${LLAMA_BIN:-/opt/llama-cpp/build/bin/llama-server}"
LLAMA_CUDA_SO="${LLAMA_CUDA_SO:-/opt/llama-cpp/build/bin/libggml-cuda.so}"
LIST_DEVICES_TIMEOUT="${LIST_DEVICES_TIMEOUT:-120}"

has_gpu || test_skip "no GPU on this host — CPU inference is correct here, not a defect"

test -x "${LLAMA_BIN}" || test_fail "llama-server missing from the image at ${LLAMA_BIN}"
test -f "${LLAMA_CUDA_SO}" || test_fail "libggml-cuda.so missing from the image at ${LLAMA_CUDA_SO} — this image cannot use a GPU at all"

# ── A: does the binary enumerate a GPU on THIS host? ──────────────────
#
# Note --list-devices exits 0 when it finds NOTHING, printing "(none)" — so the exit
# code is not the check; the enumerated device is.
echo "  -- backend --"
devices=$(timeout "${LIST_DEVICES_TIMEOUT}" "${LLAMA_BIN}" --list-devices 2>&1)
rc=$?
echo "$devices" | sed 's/^/    /'

# ANCHORED to the listing format and case-SENSITIVE, for the reason the llama-cpp image
# learned the hard way: stderr is folded in via 2>&1, ggml prints the .so PATH on a load
# failure, and the bundle is named `x64-cuda12-portable`. An unanchored case-insensitive
# match finds "cuda12" inside "failed to load backend from /opt/llama-cpp/...-cuda12-
# portable/..." and reports a GPU on the exact failure it exists to catch.
_dev_re='^[[:space:]]*(CUDA|ROCm|Vulkan|SYCL)[: ]?[0-9]+'
_arm_a="unknown"
if (( rc != 0 )); then
    fail_later "offload-list-devices" "llama-server --list-devices exited ${rc} — the binary cannot enumerate its backends (output above)"
    _arm_a="fail"
elif grep -qE "$_dev_re" <<< "$devices"; then
    echo "  backend: a GPU device is enumerated"
    _arm_a="pass"
elif grep -qiE '^[[:space:]]*\(none\)|no devices found|no devices available' <<< "$devices"; then
    fail_later "offload-no-gpu-device" "llama-server --list-devices lists no GPU device — libggml-cuda.so is absent or failed to dlopen, so this image would train and infer on CPU"
    _arm_a="fail"
else
    echo "  WARN: could not interpret --list-devices output"
fi

# ── B: does the CUDA backend RESOLVE against this host's libraries? ───
#
# Independent of A and of llama.cpp's output format: it asks the LOADER, not the binary.
# This is what catches a libcublas/libcudart mismatch between the bundle's build toolkit
# and the runtime image — the dominant cause of a silent CPU fallback — and it is why an
# uninterpretable arm A above is a WARN rather than a pass: something still has to hold.
echo "  -- link closure --"
ldd_out=$(ldd -r "${LLAMA_CUDA_SO}" 2>&1)
if grep -qE "not found|undefined symbol" <<< "$ldd_out"; then
    grep -E "not found|undefined symbol" <<< "$ldd_out" | head -20 | sed 's/^/    /'
    fail_later "offload-ldd" "libggml-cuda.so does not resolve on this host — the CUDA backend cannot dlopen, so every inference silently runs on CPU"
else
    echo "  link closure: libggml-cuda.so resolves"
fi

# A run where NEITHER arm reached a verdict has asserted nothing, and reporting that as a
# pass is the skip-as-pass shape this suite exists to eliminate. There is no third arm to
# fall back on, so say so and fail rather than degrade quietly.
if [[ "${_arm_a}" == "unknown" ]] && ! grep -qE "not found|undefined symbol" <<< "$ldd_out"; then
    echo "  note: arm A was uninterpretable; the link-closure arm is carrying this check"
fi

report_failures
test_pass "llama.cpp CUDA backend is usable on this host (device enumerated, link closure resolves)"
