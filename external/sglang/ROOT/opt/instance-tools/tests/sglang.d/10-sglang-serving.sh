#!/bin/bash
# Test: SGLang serving pipeline — service, API readiness, and inference.
# TEST_TIMEOUT=7200
source "$(dirname "$0")/../lib.sh"

# ── Constants ────────────────────────────────────────────────────────
SGLANG_INTERNAL_PORT=18000        # SGLang listens here (localhost); Caddy proxies 8000 with auth
SGLANG_LOG="/var/log/sglang.log"
HEALTH_TIMEOUT="${SGLANG_HEALTH_TIMEOUT:-3600}"  # 1 hour for model loading + warmup
MAX_RESTARTS=2                                   # fail after this many supervisor restarts (crash loop)

# ── Skip if SGLang is not configured ─────────────────────────────────

[[ -n "${SGLANG_MODEL:-}" ]] || test_skip "SGLANG_MODEL not set"
echo "  SGLANG_MODEL=${SGLANG_MODEL}"
echo "  SGLANG_ARGS=${SGLANG_ARGS:-<empty>}"

# ── Helper: check if PORTAL_CONFIG has an entry whose label matches ──

portal_config_has() {
    local pattern="$1"
    [[ -n "${PORTAL_CONFIG:-}" ]] && echo "$PORTAL_CONFIG" | tr '|' '\n' | grep -qiE "$pattern"
}

# ── SGLang service ───────────────────────────────────────────────────

echo ""
echo "  -- sglang --"

assert_service_running sglang
echo "  sglang: supervisor service running"

# Check for early fatal errors in the log before waiting for the API.
if [[ -f "$SGLANG_LOG" ]]; then
    fatal_patterns="Traceback|CUDA out of memory|RuntimeError:|OSError:|ValueError:|Failed to|Cannot find"
    early_errors=$(head -100 "$SGLANG_LOG" | grep -cE "$fatal_patterns" || true)
    if [[ "$early_errors" -gt 0 ]]; then
        echo "  WARN: ${early_errors} potential error(s) in early sglang log — may be transient"
        head -100 "$SGLANG_LOG" | grep -E "$fatal_patterns" | head -5 | sed 's/^/    /'
    fi
fi

# ── Wait for SGLang API health ───────────────────────────────────────

echo ""
echo "  -- api health --"

HEALTH_URL="http://127.0.0.1:${SGLANG_INTERNAL_PORT}/health"
elapsed=0
healthy=false
last_report=0
restart_count=0
last_pid=""

# Get initial SGLang PID from supervisor
last_pid=$(supervisorctl status sglang 2>/dev/null | grep -oP 'pid \K[0-9]+' || true)

_dump_log_tail() {
    if [[ -f "$SGLANG_LOG" ]]; then
        echo "  last 10 lines of sglang log:"
        tail -10 "$SGLANG_LOG" | sed 's/^/    /'
    fi
}

while (( elapsed < HEALTH_TIMEOUT )); do
    status=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null)
    if [[ "$status" == "200" ]]; then
        healthy=true
        break
    fi

    # Track supervisor restarts by PID change
    sup_line=$(supervisorctl status sglang 2>/dev/null)
    sglang_sup_state=$(echo "$sup_line" | awk '{print $2}')
    cur_pid=$(echo "$sup_line" | grep -oP 'pid \K[0-9]+' || true)

    if [[ "$sglang_sup_state" == "FATAL" ]]; then
        echo "  sglang entered FATAL state — supervisor gave up restarting"
        _dump_log_tail
        test_fail "sglang service FATAL (supervisor stopped restarting)"
    fi

    if [[ "$sglang_sup_state" != "RUNNING" && "$sglang_sup_state" != "STARTING" ]]; then
        echo "  sglang service in state: ${sglang_sup_state}"
        _dump_log_tail
        test_fail "sglang service not running (state: ${sglang_sup_state})"
    fi

    # Detect restart: PID changed while we were waiting
    if [[ -n "$last_pid" && -n "$cur_pid" && "$cur_pid" != "$last_pid" ]]; then
        restart_count=$((restart_count + 1))
        echo "  sglang restarted (pid ${last_pid} → ${cur_pid}, restart #${restart_count})"
        _dump_log_tail
        if (( restart_count >= MAX_RESTARTS )); then
            test_fail "sglang crash loop: ${restart_count} restarts detected (bad config or OOM?)"
        fi
    fi
    last_pid="$cur_pid"

    # Progress report every 30s
    if (( elapsed - last_report >= 30 )); then
        last_report=$elapsed
        log_size=$(stat -c '%s' "$SGLANG_LOG" 2>/dev/null || echo "0")
        echo "  [${elapsed}s] waiting for /health (log=${log_size}B, http=${status}, restarts=${restart_count})"
    fi

    sleep 5
    elapsed=$((elapsed + 5))
done

if ! $healthy; then
    echo "  SGLang API did not become healthy within ${HEALTH_TIMEOUT}s"
    if [[ -f "$SGLANG_LOG" ]]; then
        echo "  last 15 lines of sglang log:"
        tail -15 "$SGLANG_LOG" | sed 's/^/    /'
    fi
    test_fail "SGLang API /health not reachable after ${HEALTH_TIMEOUT}s"
fi

echo "  SGLang API healthy after ${elapsed}s (${restart_count} restart(s))"

# ── Verify /v1/models ────────────────────────────────────────────────

echo ""
echo "  -- model verification --"

models_json=$(curl -sf --max-time 10 "http://127.0.0.1:${SGLANG_INTERNAL_PORT}/v1/models" 2>/dev/null)
if [[ -z "$models_json" ]]; then
    fail_later "v1-models" "/v1/models returned empty response"
else
    model_info=$(echo "$models_json" | python3 -c "
import sys, json, os
d = json.load(sys.stdin)
models = d.get('data', [])
print(len(models))
for m in models:
    print(m.get('id', '?'))
want = os.environ.get('SGLANG_MODEL', '')
ids = [m.get('id', '') for m in models]
found = any(want in mid or mid in want for mid in ids)
print('found' if found else 'missing')
" 2>/dev/null)

    count=$(echo "$model_info" | head -1)
    match=$(echo "$model_info" | tail -1)

    if [[ "${count:-0}" -gt 0 ]]; then
        echo "  /v1/models: ${count} model(s) loaded"
        echo "$model_info" | sed '1d;$d' | sed 's/^/    /'
    else
        fail_later "v1-models-empty" "/v1/models returned 0 models"
    fi

    if [[ "$match" == "found" ]]; then
        echo "  SGLANG_MODEL found in /v1/models"
    else
        echo "  WARN: SGLANG_MODEL '${SGLANG_MODEL}' not found verbatim in /v1/models (may use a different ID)"
    fi
fi

# ── Log error check ──────────────────────────────────────────────────

echo ""
echo "  -- log analysis --"

check_log_errors() {
    local label="$1" path="$2" exclude="${3:-}"
    [[ -f "$path" ]] || return 0
    local error_lines
    if [[ -n "$exclude" ]]; then
        error_lines=$(grep -P "\b(ERROR|CRITICAL)\b" "$path" 2>/dev/null \
            | grep -vE "$exclude" || true)
    else
        error_lines=$(grep -P "\b(ERROR|CRITICAL)\b" "$path" 2>/dev/null || true)
    fi
    local error_count
    error_count=$(echo "$error_lines" | grep -c . 2>/dev/null || true)
    if [[ "$error_count" -gt 0 ]]; then
        echo "  ${error_count} ERROR/CRITICAL line(s) in ${label} log:"
        echo "$error_lines" | tail -5 | sed 's/^/    /'
        fail_later "${label}-log-errors" "${error_count} ERROR/CRITICAL line(s) in ${label} log"
    else
        echo "  ${label} log clean"
    fi
}

if [[ -f "$SGLANG_LOG" ]]; then
    log_size=$(stat -c '%s' "$SGLANG_LOG" 2>/dev/null || echo "0")
    echo "  sglang log: ${log_size}B"
fi

check_log_errors "sglang" "$SGLANG_LOG" "deprecat"

# ── Port exposure check ──────────────────────────────────────────────

echo ""
echo "  -- port exposure --"

declare -A PORT_CHECKS=(
    ["sglang"]="8000:SGLang API:sglang"
)

if is_serverless; then
    echo "  skip: port exposure checks (caddy not running in serverless)"
else
    for key in "${!PORT_CHECKS[@]}"; do
        IFS=: read -r port label pattern <<< "${PORT_CHECKS[$key]}"
        if portal_config_has "$pattern"; then
            if ss -tln | grep -q ":${port} "; then
                echo "  ${label}: listening on port ${port}"
            else
                fail_later "port-${port}" "${label} in PORTAL_CONFIG but port ${port} not listening"
            fi
        else
            echo "  skip: ${label} (not in PORTAL_CONFIG)"
        fi
    done
fi

# ── Inference check ──────────────────────────────────────────────────
# SGLANG_TEST_ENDPOINT controls inference testing:
#   "chat"  (default) — test via /v1/chat/completions
#   "none"  — skip inference test (for embedding/reranker models)

echo ""
echo "  -- inference --"

SGLANG_TEST_ENDPOINT="${SGLANG_TEST_ENDPOINT:-chat}"
case "$SGLANG_TEST_ENDPOINT" in
    chat|none) ;;
    *) test_fail "unsupported SGLANG_TEST_ENDPOINT='${SGLANG_TEST_ENDPOINT}' (must be 'chat' or 'none')" ;;
esac
SGLANG_API="http://127.0.0.1:${SGLANG_INTERNAL_PORT}"
# Use the model ID that SGLang is actually serving (from /v1/models)
SERVED_MODEL=$(curl -sf --max-time 10 "${SGLANG_API}/v1/models" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null)
SERVED_MODEL="${SERVED_MODEL:-${SGLANG_MODEL}}"

if [[ "${SGLANG_TEST_ENDPOINT}" == "none" ]]; then
    echo "  inference test skipped (SGLANG_TEST_ENDPOINT=none)"
else
    echo "  endpoint: ${SGLANG_TEST_ENDPOINT} (/v1/chat/completions)"
    echo "  model: ${SERVED_MODEL}"

    # Warmup: /health goes green before the engine is fully warm (graph
    # capture, CUDA autotune, lazy init of the first batch). Send a
    # throwaway request so the scored prompts run on a hot server.
    # Failures here are tolerated — the scored loop is what we judge on.
    warmup_body=$(mktemp)
    warmup_code=$(curl -s --max-time 300 -o "$warmup_body" -w '%{http_code}' \
        "${SGLANG_API}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json, sys
print(json.dumps({
    'model': sys.argv[1],
    'messages': [{'role': 'user', 'content': 'hi'}],
    'temperature': 0.1,
    'max_tokens': 8
}))
" "$SERVED_MODEL")" 2>/dev/null)
    rm -f "$warmup_body"
    echo "  warmup: HTTP ${warmup_code}"

    # Test prompts — exercise different aspects of the model.
    # We only care that the server ran inference (produced completion tokens),
    # not that the content looks right. Reasoning models may emit empty
    # `content` / `reasoning_content` and still be serving correctly, so
    # completion_tokens > 0 is the pass signal.
    declare -a PROMPTS=(
        "Say hello in one sentence."
        "What is 2+2? Reply with just the number."
        "Write a haiku about computers."
    )
    inference_pass=0
    inference_fail=0

    for i in "${!PROMPTS[@]}"; do
        prompt="${PROMPTS[$i]}"
        echo ""
        echo "  request $((i+1))/${#PROMPTS[@]}: ${prompt}"

        http_body=$(mktemp)
        http_code=$(curl -s --max-time 180 -o "$http_body" -w '%{http_code}' \
            "${SGLANG_API}/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d "$(python3 -c "
import json, sys
print(json.dumps({
    'model': sys.argv[1],
    'messages': [{'role': 'user', 'content': sys.argv[2]}],
    'temperature': 0.1,
    'max_tokens': 256
}))
" "$SERVED_MODEL" "$prompt")" 2>/dev/null)
        response=$(cat "$http_body")
        rm -f "$http_body"

        if [[ "$http_code" != "200" ]]; then
            echo "    FAIL: HTTP ${http_code}"
            [[ -n "$response" ]] && echo "    body: ${response:0:200}"
            inference_fail=$((inference_fail + 1))
            continue
        fi

        eval "$(echo "$response" | python3 -c "
import sys, json, shlex
try:
    d = json.load(sys.stdin)
    c = (d.get('choices') or [{}])[0]
    m = c.get('message', {})
    content = (m.get('content') or '').strip()
    reasoning = (m.get('reasoning_content') or m.get('thinking_content') or '').strip()
    finish = c.get('finish_reason', '?')
    usage = d.get('usage', {})
    prompt_tok = usage.get('prompt_tokens', 0)
    compl_tok = usage.get('completion_tokens', 0)
    msg = content or reasoning
    kind = 'content' if content else ('reasoning' if reasoning else 'none')
    print(f'finish_reason={shlex.quote(str(finish))}')
    print(f'prompt_tokens={shlex.quote(str(prompt_tok))}')
    print(f'compl_tokens={shlex.quote(str(compl_tok))}')
    print(f'content={shlex.quote(msg)}')
    print(f'content_kind={shlex.quote(kind)}')
    print(f'parse_ok=true')
except Exception:
    print('finish_reason=error prompt_tokens=0 compl_tokens=0 content= content_kind=none parse_ok=false')
" 2>/dev/null)"

        if [[ "${parse_ok:-false}" != "true" ]]; then
            echo "    FAIL: malformed response"
            echo "    raw: ${response:0:200}"
            inference_fail=$((inference_fail + 1))
            continue
        fi

        if (( ${compl_tokens:-0} > 0 )); then
            if [[ -n "$content" ]]; then
                display="${content:0:120}"
                [[ ${#content} -gt 120 ]] && display="${display}..."
                [[ "$content_kind" == "reasoning" ]] && echo "    (reasoning_content/thinking_content only)"
                echo "    response: ${display}"
            else
                echo "    (no visible content — reasoning/empty output, but inference ran)"
            fi
            echo "    tokens: ${prompt_tokens} prompt → ${compl_tokens} completion, finish=${finish_reason}"
            inference_pass=$((inference_pass + 1))
        else
            echo "    FAIL: 0 completion tokens (finish_reason=${finish_reason})"
            echo "    raw: ${response:0:200}"
            inference_fail=$((inference_fail + 1))
        fi
    done

    echo ""
    echo "  inference: ${inference_pass}/${#PROMPTS[@]} produced completion tokens, ${inference_fail} failed"
    if (( inference_pass == 0 )); then
        fail_later "inference" "no prompts produced completion tokens (${inference_fail}/${#PROMPTS[@]} failed)"
    elif (( inference_fail > 0 )); then
        echo "  WARN: ${inference_fail} request(s) failed, but server is serving inference"
    fi
fi

# ── Report ───────────────────────────────────────────────────────────

report_failures
test_pass "SGLang serving pipeline verified (model: ${SGLANG_MODEL}, health: ${elapsed}s)"
