#!/bin/bash
# Test: oobabooga (Text Generation WebUI) serving + generation.
#
# oobabooga.sh launches server.py with --listen-port 17860 (WebUI) and
# --api --api-port 15000 (OpenAI-compatible API), both bound to 127.0.0.1
# (no --listen; the 30-networking base test covers the no-0.0.0.0 bind).
#
# Health (WebUI 200 + API model list) is a hard prerequisite. Generation is
# ENFORCED — a real /v1/completions must produce completion tokens — matching
# how the vLLM and ComfyUI tests verify actual functionality, not just health.
# Like vLLM (which skips when VLLM_MODEL is unset), this skips ONLY when no
# model is loaded to exercise; the model is template/provisioning-supplied (the
# image bakes none, but a model loads by default). We assert that inference
# RAN (completion_tokens > 0), not that the content is "right".
# TEST_TIMEOUT=3600
source "$(dirname "$0")/../lib.sh"

UI_PORT="${OOBABOOGA_UI_PORT:-17860}"
API_PORT="${OOBABOOGA_API_PORT:-15000}"
HEALTH_TIMEOUT="${OOBABOOGA_HEALTH_TIMEOUT:-1800}"
GEN_TIMEOUT="${OOBABOOGA_GEN_TIMEOUT:-180}"
UI="http://127.0.0.1:${UI_PORT}"
API="http://127.0.0.1:${API_PORT}"

service_running oobabooga || test_skip "oobabooga service not running"

# ── WebUI on its loopback port (hard prerequisite) ───────────────────
echo "  -- waiting for the WebUI on ${UI} --"
wait_for_port "$UI_PORT" "$HEALTH_TIMEOUT" \
    || test_fail "WebUI not listening on ${UI_PORT} within ${HEALTH_TIMEOUT}s"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${UI}/" 2>/dev/null)
[[ "$code" == "200" ]] || test_fail "WebUI not served at ${UI}/ (HTTP ${code})"
echo "  WebUI served at ${UI}/ (HTTP 200)"

# ── OpenAI-compatible API on its loopback port (hard prerequisite) ───
echo "  -- waiting for the API on ${API} --"
wait_for_port "$API_PORT" "$HEALTH_TIMEOUT" \
    || test_fail "API not listening on ${API_PORT} within ${HEALTH_TIMEOUT}s"
echo "$(curl -s --max-time 15 "${API}/v1/models" 2>/dev/null)" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('object')=='list', 'not an OpenAI-style model list'" 2>/dev/null \
    || test_fail "API ${API}/v1/models did not return a valid OpenAI model list"
echo "  API model list served at ${API}/v1/models"

# ── Loaded model (the thing to exercise) ─────────────────────────────
# /v1/internal/model/info reports the CURRENTLY-LOADED model. Skip — don't
# pass — when none is loaded: there is no functionality to verify (vLLM skips
# the same way when VLLM_MODEL is unset). A normal launch loads a default.
loaded=$(curl -s --max-time 15 "${API}/v1/internal/model/info" 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(''); raise SystemExit
n = d.get('model_name') or ''
print('' if n in ('', 'None') else n)" 2>/dev/null)

[[ -n "$loaded" ]] \
    || test_skip "no model loaded — set a model (e.g. OOBABOOGA_ARGS=\"--model <name>\") to exercise generation"
echo "  loaded model: ${loaded}"

# ── ENFORCED generation ──────────────────────────────────────────────
echo "  -- generation (/v1/completions) --"
body=$(mktemp)
http_code=$(curl -s --max-time "$GEN_TIMEOUT" -o "$body" -w '%{http_code}' \
    "${API}/v1/completions" \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"The Vast.ai platform provides","max_tokens":16,"temperature":0.1}' 2>/dev/null)

if [[ "$http_code" != "200" ]]; then
    echo "  response: $(head -c 300 "$body")"
    rm -f "$body"
    test_fail "generation failed: HTTP ${http_code} from ${API}/v1/completions (model ${loaded})"
fi

# Inference RAN if it produced completion tokens — content is not judged
# (a tiny default model emits gibberish but is still serving), matching vLLM.
toks=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
ch = (d.get('choices') or [{}])[0]
assert ch.get('text') is not None, 'no completion text in response'
print(d.get('usage', {}).get('completion_tokens', 0) or 0)" "$body" 2>/dev/null)
rm -f "$body"

[[ "${toks:-0}" =~ ^[0-9]+$ ]] && (( toks > 0 )) \
    || test_fail "generation produced 0 completion tokens (model ${loaded}) — inference did not run"
echo "  generation OK: ${toks} completion token(s) from ${loaded}"

test_pass "oobabooga serving + generation verified (WebUI ${UI_PORT} + OpenAI API ${API_PORT} on loopback; ${toks} tokens on ${loaded})"
