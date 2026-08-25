#!/bin/bash
# Test: the CUDA backend is ACTUALLY SERVING — not merely present (L076, ADR 0016).
#
# llama.cpp is built `ggml_backend_dl: ON`, which means the CUDA backend is a
# dlopen'd plugin rather than a link-time dependency. When that dlopen fails,
# llama-server does not crash and does not exit non-zero. It falls back to the CPU
# backend and serves correct answers, slowly, forever.
#
# Nothing else in this suite can see that. 10-llama-serving asserts the service is up,
# /health returns 200, /v1/models is non-empty and a prompt returns tokens.
# 12-llama-contract asserts token arithmetic, a grammar, a named tool, a status class
# and a bind address. 20-serverless-pyworker asserts a benchmark score was written.
# A 0.5B q8_0 GGUF answers every one of those from CPU in seconds, so before this file
# existed a fully CPU-only image passed the entire gate on every cell — the gate
# certified a GPU image that was not using the GPU.
#
# The dlopen fails for reasons no other check sees: a libcublas minor the bundle was
# not built against, a driver too old for the bundle's CUDA major, or a host compute
# capability with neither a cubin nor JITable PTX in the binary. All three are silent.
#
# L056 is the build-time half of this invariant and does NOT reach here: it triggers
# on `unsloth studio setup`, so any image installing a PREBUILT bundle is exempt from
# it by construction. This is the runtime half.
# TEST_TIMEOUT=1800
source "$(dirname "$0")/../lib.sh"

LLAMA_INTERNAL_PORT="${LLAMA_INTERNAL_PORT:-18000}"
# Read the port the template actually launched with, so a template that moves the
# engine does not probe an empty socket and report the backend missing.
_declared_port=$(sed -n 's/.*--port[= ]\+\([0-9]\+\).*/\1/p' <<< "${LLAMA_ARGS:-}" | head -1)
[[ -n "$_declared_port" ]] && LLAMA_INTERNAL_PORT="$_declared_port"

# The clean log, with the portal log as fallback: logging.sh writes the portal copy and
# log-tee derives /var/log/<name>.log from it, so on a healthy box both exist and the
# clean one is the one worth grepping.
LLAMA_LOG=/var/log/llama.log
[[ -f "$LLAMA_LOG" ]] || LLAMA_LOG=/var/log/portal/llama.log

READY_TIMEOUT="${LLAMA_OFFLOAD_READY_TIMEOUT:-1200}"

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
# "the image is broken" from "this particular launch did not use the GPU".
echo ""
echo "  -- backend --"
devices=$(timeout 60 llama-server --list-devices 2>&1)
rc=$?
echo "$devices" | sed 's/^/    /'
if (( rc != 0 )); then
    fail_later "offload-list-devices" "llama-server --list-devices exited ${rc} — the binary cannot enumerate its backends (output above)"
elif ! grep -qiE '(CUDA|ROCm|Vulkan)[0-9]' <<< "$devices"; then
    fail_later "offload-no-gpu-device" "llama-server --list-devices lists no GPU device — libggml-cuda.so is absent or failed to dlopen, so this image serves on CPU (check: ldd -r on \$LLAMA_CPP_DIR/libggml-cuda.so, and the driver's CUDA major against the bundle's)"
else
    echo "  backend: a GPU device is enumerated"
fi

# ── B: did the RUNNING server use it? ────────────────────────────────
#
# A binary that CAN load CUDA and a server that DID are different claims, and only the
# second one is what the customer is paying for. Both log forms are accepted because
# llama.cpp has printed each across versions; requiring one exact string would turn an
# upstream wording change into a red gate. Requiring at least one of them is the point —
# silence must never read as success.
echo ""
echo "  -- offload --"
if [[ ! -f "$LLAMA_LOG" ]]; then
    fail_later "offload-no-log" "neither /var/log/llama.log nor /var/log/portal/llama.log exists — cannot confirm what the running server loaded"
else
    cuda_init=$(grep -ciE 'ggml_cuda_init|found [0-9]+ (CUDA|ROCm) devices?|using (CUDA|ROCm)[0-9]' "$LLAMA_LOG" || true)
    offload_line=$(grep -iE 'offloa(ded|ding) .*(layers?|repeating).* to GPU' "$LLAMA_LOG" | tail -3)

    [[ -n "$offload_line" ]] && sed 's/^/    /' <<< "$offload_line"
    echo "  CUDA init lines in log: ${cuda_init}"

    if (( cuda_init == 0 )) && [[ -z "$offload_line" ]]; then
        fail_later "offload-cpu-fallback" "the running llama-server logged no CUDA initialisation and no layer offload — it is serving from CPU (this is the ggml_backend_dl silent-fallback path; /health, /v1/models and completions all still pass in this state)"
    fi

    # Only asserted when the line is present. A version that stops printing it is
    # caught above; a version that prints "offloaded 0/29" is caught here, and that
    # is the shape a too-small VRAM budget or a bad -ngl produces.
    if [[ -n "$offload_line" ]]; then
        n=$(sed -n 's/.*offloaded \([0-9]\+\)\/\([0-9]\+\) layers.*/\1/p' <<< "$offload_line" | tail -1)
        if [[ -n "$n" ]] && (( n == 0 )); then
            fail_later "offload-zero-layers" "llama-server offloaded 0 layers to GPU — the backend loaded but the model did not (VRAM budget, or -ngl/--n-gpu-layers set to 0)"
        fi
    fi
fi

# ── Corroboration, deliberately NOT gating ───────────────────────────
#
# nvidia-smi's per-process view is unreliable inside a container: compute-app
# attribution depends on the PID namespace, and an empty list is routinely returned on
# a GPU that is genuinely busy. Gating on it would produce a flaky red, and a flaky
# gate teaches people to re-run rather than to look. Printed because when the
# assertions above DO fail, this is the first thing the person reading the log wants.
echo ""
echo "  -- corroboration (not gating) --"
if command -v nvidia-smi &>/dev/null; then
    used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | head -2 | tr '\n' ' ')
    echo "  GPU memory used: ${used:-<unavailable>}"
    apps=$(nvidia-smi --query-compute-apps=pid,used_gpu_memory --format=csv,noheader 2>/dev/null | head -5)
    echo "  compute apps: ${apps:-<none visible in this PID namespace>}"
else
    echo "  nvidia-smi not present"
fi

report_failures
test_pass "llama.cpp CUDA backend verified in use — the served model is on the GPU"
