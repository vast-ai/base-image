#!/bin/bash
# Test: the CUDA backend is ACTUALLY SERVING — not merely present (L076, ADR 0016).
#
# llama.cpp is built `ggml_backend_dl: ON`, which means the CUDA backend is a
# dlopen'd plugin. When that dlopen fails, llama-server does not crash and does not
# exit non-zero. It falls back to the CPU backend and serves correct answers, slowly,
# forever.
#
# Nothing else in this suite can see that. 10-llama-serving asserts the service is up,
# /health returns 200, /v1/models is non-empty and a prompt returns tokens.
# 12-llama-contract asserts token arithmetic, a grammar, a named tool, a status class
# and a bind address. 20-serverless-pyworker asserts a benchmark score was written.
# A 0.5B q8_0 GGUF answers every one of those from CPU in seconds, so before this file
# existed a fully CPU-only image passed the entire gate on every cell.
#
# The dlopen fails for causes no other check sees: a libcublas minor the bundle was
# not built against, a driver too old for the bundle's CUDA major, or a host compute
# capability with neither a cubin nor JITable PTX in the binary. All three are silent.
#
# L056 is the build-time half of this invariant and does NOT reach here: it triggers
# on `unsloth studio setup`, so any image installing a PREBUILT bundle is exempt from
# it by construction. This is the runtime half.
#
# WHAT IS **NOT** ASSERTED HERE, AND WHY — read before "fixing" it:
# The server's LOG is not an instrument for this. The first version of this file gated
# on `ggml_cuda_init: found N CUDA devices` / `load_tensors: offloaded N/N layers to
# GPU`, which is the wording llama.cpp printed for years. Upstream's v0.2.0 rewrote the
# logging: at verbosity 3 it goes straight from `srv load_model: loading model '...'`
# to `srv llama_server: model loaded`, and prints NO device or offload line at all.
# Measured on run 32835411583 — the check failed on a box where nvidia-smi showed the
# model resident in 836 MiB of VRAM. A gating assertion whose evidence upstream is free
# to stop printing is a false-red generator, so the log is now diagnostic only.
# TEST_TIMEOUT=1800
source "$(dirname "$0")/../lib.sh"

LLAMA_INTERNAL_PORT="${LLAMA_INTERNAL_PORT:-18000}"
# Read the port the template actually launched with, so a template that moves the
# engine does not probe an empty socket and report the backend missing.
_declared_port=$(sed -n 's/.*--port[= ]\+\([0-9]\+\).*/\1/p' <<< "${LLAMA_ARGS:-}" | head -1)
[[ -n "$_declared_port" ]] && LLAMA_INTERNAL_PORT="$_declared_port"

LLAMA_LOG=/var/log/llama.log
[[ -f "$LLAMA_LOG" ]] || LLAMA_LOG=/var/log/portal/llama.log

READY_TIMEOUT="${LLAMA_OFFLOAD_READY_TIMEOUT:-3600}"   # matches 10-llama-serving's cold-start budget
# Baked numbers can only be corrected by rebuilding and re-promoting (L070). Cold CUDA
# context init on a many-GPU host with persistence mode off can exceed a minute.
LIST_DEVICES_TIMEOUT="${LLAMA_LIST_DEVICES_TIMEOUT:-120}"
NVIDIA_SMI_TIMEOUT="${LLAMA_NVIDIA_SMI_TIMEOUT:-60}"

# Inert, not skip-as-pass: with no model there is no load to offload. 10-llama-serving
# is the file the gate REQUIRES alongside this one, so an image that lost LLAMA_MODEL is
# already red there (L057/L072) rather than quietly green here.
[[ -n "${LLAMA_MODEL:-}" ]] || test_skip "LLAMA_MODEL not set — nothing is loaded, so there is no offload to assert"
has_gpu || test_skip "no GPU on this host — CPU inference is correct here, not a defect"

# Presence for IDENTITY only, and only after the supervisord socket has been reached
# (L069): assert_service_running goes through the RPC socket, so by the time we ask
# which pid is llama-server, supervisord is genuinely usable.
assert_service_running llama
echo "  llama: supervisor service running"

echo ""
echo "  -- readiness --"
# Offload happens during model load, so every assertion below is meaningless until the
# server is up. Sized for a cold start: this normally runs after 10-llama-serving has
# already waited out the load, but it must also hold when run alone over SSH.
wait_for_url "http://127.0.0.1:${LLAMA_INTERNAL_PORT}/health" "$READY_TIMEOUT" \
    || test_fail "llama /health not reachable on :${LLAMA_INTERNAL_PORT} after ${READY_TIMEOUT}s — cannot judge offload on a server that never loaded"
echo "  llama healthy on :${LLAMA_INTERNAL_PORT}"

# ── A: can the backend load at all? ──────────────────────────────────
#
# Asks the BINARY, independently of the running server, so a failure here separates
# "the image is broken" from "this particular launch did not use the GPU". This is the
# portable half: it depends on nothing but the binary and the driver, and it is what
# catches the dominant failures (missing libcublas, wrong CUDA major, no kernel image).
#
# Note --list-devices exits 0 when it finds NOTHING, printing "Available devices:" and
# "(none)" — verified against the shipped binary. So the exit code is not the check;
# the enumerated device is.
echo ""
echo "  -- backend --"
devices=$(timeout "$LIST_DEVICES_TIMEOUT" llama-server --list-devices 2>&1)
rc=$?
echo "$devices" | sed 's/^/    /'

# ANCHORED to the listing format, and case-SENSITIVE. The obvious pattern
# (`grep -qiE '(CUDA|ROCm|Vulkan)[0-9]'`) matches the wrong thing in the one state
# this arm exists to catch: stderr is folded in via 2>&1, ggml's loader prints the
# .so PATH on failure, and ADR 0033's bundles are named `x64-cuda12-portable`. A
# case-insensitive unanchored match would find "cuda12" inside
# "failed to load backend from /opt/llama.cpp/x64-cuda12-portable/libggml-cuda.so"
# and report a GPU as enumerated on the exact failure it is looking for.
# No trailing colon: `CUDA:0 NVIDIA H100` is a plausible rename and would false-red.
# The LINE ANCHOR is what does the safety work, not the punctuation — an error message
# begins with "failed to load backend from ..." or a "/opt/..." path, never with a
# device label — and the match is case-SENSITIVE so a lowercase `cuda12-portable` path
# segment cannot match even if one ever did start a line.
_dev_re='^[[:space:]]*(CUDA|ROCm|Vulkan|SYCL)[: ]?[0-9]+'
if (( rc != 0 )); then
    fail_later "offload-list-devices" "llama-server --list-devices exited ${rc} — the binary cannot enumerate its backends (output above)"
elif grep -qE "$_dev_re" <<< "$devices"; then
    echo "  backend: a GPU device is enumerated"
elif grep -qiE '^[[:space:]]*\(none\)|no devices found|no devices available' <<< "$devices"; then
    fail_later "offload-no-gpu-device" "llama-server --list-devices lists no GPU device — libggml-cuda.so is absent or failed to dlopen, so this image serves on CPU (check: ldd -r on \$LLAMA_CPP_DIR/libggml-cuda.so, and the driver's CUDA major against the bundle's)"
else
    # Neither a device line nor an explicit empty listing. That is an unrecognised
    # output format, not a verdict — and hard-failing on it would repeat the mistake
    # this file already made once, gating on a string upstream is free to change.
    # Arm B is independent and still gating, so degrade rather than red.
    echo "  WARN: could not interpret --list-devices output — arm B is carrying this check"
fi

# ── B: did the RUNNING server put the model there? ───────────────────
#
# A binary that CAN load CUDA and a server that DID are different claims, and only the
# second is what the customer pays for. This asks the DRIVER, which is the one source
# neither an upstream log-format change nor a llama.cpp release can invalidate.
#
# An earlier revision demoted this to non-gating on the theory that compute-app
# attribution is unreliable inside a container because of PID-namespace isolation. That
# is a real effect in general and it is NOT what happens here: measured on run
# 32835411583, `--query-compute-apps` returned our own pid and 836 MiB from inside the
# instance. The general caution cost the only assertion that worked.
echo ""
echo "  -- offload --"

# Device-total VRAM, read FIRST because it is the discriminator for everything below.
# It is namespace-PROOF: it asks the device, not the process table, so it answers even
# where compute-app enumeration cannot.
dev_used=$(timeout "$NVIDIA_SMI_TIMEOUT" nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -dc '0-9')
apps=$(timeout "$NVIDIA_SMI_TIMEOUT" nvidia-smi --query-compute-apps=pid,used_gpu_memory --format=csv,noheader 2>/dev/null)
srv_pid=$(pgrep -o -x llama-server 2>/dev/null)
echo "  llama-server pid: ${srv_pid:-<not found>}"
echo "  compute apps: ${apps:-<none>}"
echo "  device VRAM in use: ${dev_used:-<unknown>} MiB"

# A FLOOR, not "> 0". A bare CUDA context plus cuBLAS workspace is a few hundred MiB on
# its own, so `mib > 0` passes on a server whose weights never left system RAM — which
# is the whole `-ngl 0` / partial-offload family, and the larger one. The honest floor
# is the model: if the weights are on the device, resident VRAM is at least a good
# fraction of the GGUF on disk. Derived, not guessed — and when the file cannot be
# found the check says so rather than silently falling back to a number.
gguf=$(find "${WORKSPACE:-/workspace}/llama.cpp" "${HOME}/.cache/llama.cpp" -name '*.gguf' -type f -printf '%s\n' 2>/dev/null | sort -n | tail -1)
if [[ -n "$gguf" && "$gguf" -gt 0 ]]; then
    vram_floor=$(( gguf / 1048576 / 2 ))     # half the model, in MiB
    echo "  model on disk: $(( gguf / 1048576 )) MiB — requiring >= ${vram_floor} MiB resident"
else
    vram_floor=0
    echo "  model file not found on disk — cannot derive a VRAM floor, so any non-zero residency counts"
fi

if [[ -z "$apps" ]]; then
    # Empty list has TWO causes and they need opposite verdicts. NVML process
    # enumeration is genuinely unavailable in some container configurations — that is
    # the documented PID-namespace effect, and an EMPTY list is how it usually presents,
    # not an unmatched one. Distinguishing them is exactly what dev_used is for.
    if [[ -n "$dev_used" ]] && (( dev_used > 64 )); then
        echo "  WARN: no compute-app rows, but the device holds ${dev_used} MiB — process"
        echo "        enumeration is unavailable here (PID namespace), not a CPU fallback"
    else
        fail_later "offload-no-gpu-process" "no process holds GPU memory and the device reports ${dev_used:-0} MiB in use while llama-server is serving — the model is in system RAM and inference is running on CPU (the ggml_backend_dl silent-fallback path; /health, /v1/models and completions all still pass in this state)"
    fi
elif [[ -n "$srv_pid" ]] && grep -qE "^[[:space:]]*${srv_pid}[[:space:]]*," <<< "$apps"; then
    row=$(grep -E "^[[:space:]]*${srv_pid}[[:space:]]*," <<< "$apps" | head -1)
    mib=$(sed -n 's/.*,[[:space:]]*\([0-9]\+\).*/\1/p' <<< "$row")
    if [[ -z "$mib" ]]; then
        # "934, [N/A]" — MIG and some vGPU hosts. The driver declining to report a
        # number is not the same claim as "0 MiB", and reporting it as zeroed weights
        # sends the reader to the wrong place.
        echo "  WARN: the driver did not report a memory figure for pid ${srv_pid} (row: ${row})"
    elif (( mib > vram_floor )) && (( mib > 0 )); then
        echo "  offload: llama-server (pid ${srv_pid}) holds ${mib} MiB of VRAM"
    else
        fail_later "offload-below-floor" "llama-server (pid ${srv_pid}) holds only ${mib} MiB of VRAM against a floor of ${vram_floor} MiB — the CUDA backend loaded but the WEIGHTS are not on the GPU, so a context exists and inference still runs on CPU (-ngl/--n-gpu-layers, or a VRAM budget too small for the model)"
    fi
else
    # Something holds VRAM but we cannot prove it is ours. Deliberately not a failure:
    # arm A has already proved the backend loads, and failing here would red a healthy
    # box over an attribution detail on a shared GPU.
    echo "  WARN: GPU memory is held, but not attributable to pid ${srv_pid:-<unknown>}"
    echo "        (PID-namespace skew, or another process on a shared GPU)"
fi

# ── Diagnostics, deliberately NOT gating ─────────────────────────────
#
# Both log wordings, because a box may be running either: the pre-v0.2.0 format that
# printed device init and layer offload, or the v0.2.0+ format that prints neither.
# Present for the person reading a failure, never as evidence for a verdict.
echo ""
echo "  -- diagnostics (not gating) --"
if [[ -f "$LLAMA_LOG" ]]; then
    hits=$(grep -iE 'ggml_cuda_init|found [0-9]+ (CUDA|ROCm) devices?|offloa(ded|ding) .*to GPU|load_model|model loaded' "$LLAMA_LOG" | tail -5)
    [[ -n "$hits" ]] && sed 's/^/    /' <<< "$hits" || echo "    (no matching load lines — expected on llama.cpp v0.2.0+)"
else
    echo "    no llama log at /var/log/llama.log or /var/log/portal/llama.log"
fi
used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | head -2 | tr '\n' ' ')
echo "  GPU memory used (device total): ${used:-<unavailable>}"

report_failures
test_pass "llama.cpp CUDA backend verified in use — the served model is on the GPU"
