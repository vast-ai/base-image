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

# 1. The server binds only what config.yaml says (loopback :8004). A dead launch never opens it.
wait_for_port "$PORT" 300 || test_fail "chatterbox never opened :${PORT} (supervisor/launch failed)"

# 2. Readiness: /v1/audio/voices returns 200 only once the model is loaded (503 'model is not
#    currently loaded' until the first-boot download + load completes). A model-load bug hangs here.
if ! wait_for_url "${BASE}/v1/audio/voices" "$READY_TIMEOUT"; then
    test_fail "model not ready at /v1/audio/voices within ${READY_TIMEOUT}s (model-load failure — check engine/deps, or HF_TOKEN if gated)"
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
