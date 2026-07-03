#!/bin/bash
# Test: Chatterbox serving — the TTS server answers AND actually synthesizes on the GPU.
#
# A "process is running" check is not enough here: uvicorn stays up while model-load fails,
# so synthesis 503s even though the supervisor program looks healthy. This test exercises
# the real path and so catches the model-load class of defect (wrong engine branch, a
# missing runtime dep) as well as a dead launch:
#   - dead supervisor / non-exec launch script  -> :8004 never opens (wait_for_port fails)
#   - model never loads (engine/dep bug)         -> /v1/audio/voices stays 503 (readiness times out)
#   - broken synthesis                           -> POST /v1/audio/speech is not RIFF/WAVE
#
# First boot downloads the default Turbo model (~4 GB); if that model repo is HF-gated the
# QA template must supply HF_TOKEN, or readiness will time out on a 403 (not an image bug).
#
# TEST_TIMEOUT=1800
source "$(dirname "$0")/../lib.sh"

PORT="${CHATTERBOX_PORT:-8004}"
BASE="http://127.0.0.1:${PORT}"
READY_TIMEOUT="${CHATTERBOX_READY_TIMEOUT:-1200}"   # first-boot Turbo download + load
OUT=/tmp/chatterbox-smoke.wav

has_gpu || test_skip "no GPU detected — Chatterbox synthesis requires a GPU"

# 1. Uvicorn runs the FastAPI startup — which LOADS the model — BEFORE it binds the listening
#    socket, so :8004 does NOT open until the model is ready. First boot downloads the ~4 GB
#    Turbo model, so allow the full model budget for the port to appear (a 300s wait false-fails
#    a slow-but-fine cold boot). A dead launch or a hung model-load is what times this out.
wait_for_port "$PORT" "$READY_TIMEOUT" || test_fail "chatterbox never opened :${PORT} within ${READY_TIMEOUT}s (supervisor/launch failed, or model-load hung — check chatterbox.log; set HF_TOKEN if the model repo is gated)"

# 2. Port up => startup (incl. model load) finished, so readiness is immediate. A non-200 here
#    means the model loaded with an error.
if ! wait_for_url "${BASE}/v1/audio/voices" 120; then
    test_fail "port up but /v1/audio/voices not ready — model load errored (check chatterbox.log)"
fi

# 3. Synthesize a real clip on the GPU via the OpenAI-compatible endpoint (Emily.wav is bundled).
code=$(curl -s -o "$OUT" -w '%{http_code}' -X POST "${BASE}/v1/audio/speech" \
    -H 'Content-Type: application/json' \
    -d '{"model":"tts-1","input":"Vast GPU smoke test successful.","voice":"Emily.wav","response_format":"wav"}')

[[ "$code" == "200" ]] || test_fail "synthesis returned HTTP ${code} (expected 200)"
[[ "$(head -c4 "$OUT" 2>/dev/null)" == "RIFF" ]] || test_fail "output is not RIFF/WAVE — no audio produced"
size=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
[[ "$size" -gt 20000 ]] || test_fail "audio suspiciously small (${size} bytes) — likely empty/failed synthesis"

test_pass "synthesized ${size} bytes of RIFF/WAVE audio on the GPU"
