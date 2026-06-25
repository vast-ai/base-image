#!/bin/bash
# Test: oobabooga (Text Generation WebUI) serving — the WebUI + the
# OpenAI-compatible API on their loopback ports.
#
# oobabooga.sh launches server.py with --listen-port 17860 (WebUI) and
# --api --api-port 15000 (OpenAI-compatible API), both bound to 127.0.0.1
# (no --listen; the 30-networking base test already covers the no-0.0.0.0
# bind). This test covers the piece unique to this image: that the WebUI
# serves AND the API answers on loopback — a launch/arg regression that left
# either port dead must not ship as a silently broken "healthy" service.
#
# Health (port + WebUI 200 + API model list) is GPU-agnostic. An actual
# generation needs a loaded model + a GPU, so it is GPU-gated and skips
# cleanly when there is no GPU or no model loaded (the model is template/
# provisioning-supplied, not baked into the image).
# TEST_TIMEOUT=3600
source "$(dirname "$0")/../lib.sh"

UI_PORT="${OOBABOOGA_UI_PORT:-17860}"
API_PORT="${OOBABOOGA_API_PORT:-15000}"
HEALTH_TIMEOUT="${OOBABOOGA_HEALTH_TIMEOUT:-1800}"
UI="http://127.0.0.1:${UI_PORT}"
API="http://127.0.0.1:${API_PORT}"

service_running oobabooga || test_skip "oobabooga service not running"

# ── WebUI on its loopback port ───────────────────────────────────────
echo "  -- waiting for the WebUI on ${UI} --"
wait_for_port "$UI_PORT" "$HEALTH_TIMEOUT" \
    || test_fail "WebUI not listening on ${UI_PORT} within ${HEALTH_TIMEOUT}s"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${UI}/" 2>/dev/null)
[[ "$code" == "200" ]] || test_fail "WebUI not served at ${UI}/ (HTTP ${code})"
echo "  WebUI served at ${UI}/ (HTTP 200)"

# ── OpenAI-compatible API on its loopback port ───────────────────────
echo "  -- waiting for the API on ${API} --"
wait_for_port "$API_PORT" "$HEALTH_TIMEOUT" \
    || test_fail "API not listening on ${API_PORT} within ${HEALTH_TIMEOUT}s"
models=$(curl -s --max-time 15 "${API}/v1/models" 2>/dev/null)
echo "$models" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('object')=='list', 'not an OpenAI-style model list'" 2>/dev/null \
    || test_fail "API ${API}/v1/models did not return a valid OpenAI model list"
echo "  API model list served at ${API}/v1/models"

# ── GPU-gated end-to-end generation (skip if no GPU or no model loaded) ──
gen_status="skipped (no GPU)"
if has_gpu; then
    # /v1/internal/model/info reports the CURRENTLY-LOADED model (vs /v1/models,
    # which lists every model on disk). Only exercise generation when one is
    # actually loaded — the model is template/provisioning-supplied, not baked.
    loaded=$(curl -s --max-time 15 "${API}/v1/internal/model/info" 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(''); raise SystemExit
n = d.get('model_name') or ''
print('' if n in ('', 'None') else n)" 2>/dev/null)

    if [[ -z "$loaded" ]]; then
        gen_status="skipped (no model loaded)"
    else
        echo "  -- generation exercise (loaded model: ${loaded}) --"
        body=$(curl -s --max-time 120 "${API}/v1/completions" \
            -H 'Content-Type: application/json' \
            -d '{"prompt":"Hello, world.","max_tokens":4,"temperature":0}' 2>/dev/null)
        echo "$body" | python3 -c "import json,sys; d=json.load(sys.stdin); t=(d.get('choices') or [{}])[0].get('text'); assert t is not None, 'no completion text in response'" 2>/dev/null \
            || test_fail "API ${API}/v1/completions did not produce a completion (model ${loaded})"
        echo "  generation OK (model ${loaded})"
        gen_status="OK on ${loaded}"
    fi
fi

test_pass "oobabooga serving verified (WebUI ${UI_PORT} + OpenAI API ${API_PORT} on loopback; generation: ${gen_status})"
