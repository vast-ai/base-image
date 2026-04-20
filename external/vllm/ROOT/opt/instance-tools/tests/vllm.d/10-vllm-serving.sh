#!/bin/bash
# Test: vLLM serving pipeline — Ray, vLLM service, and API readiness.
# TEST_TIMEOUT=7200
source "$(dirname "$0")/../lib.sh"

# ── Constants (adjust as needed) ──────────────────────────────────────
VLLM_INTERNAL_PORT=18000          # vLLM listens here (localhost); Caddy proxies 8000 with auth
VLLM_LOG="/var/log/vllm.log"
RAY_LOG="/var/log/ray.log"
HEALTH_TIMEOUT="${VLLM_HEALTH_TIMEOUT:-3600}"  # 1 hour for model loading + graph compilation
MAX_RESTARTS=2                                  # fail after this many supervisor restarts (crash loop)

# ── Skip if vLLM is not configured ──────────────────────────────────

[[ -n "${VLLM_MODEL:-}" ]] || test_skip "VLLM_MODEL not set"
[[ -n "${VLLM_ARGS+x}" ]] || test_fail "VLLM_ARGS is not set (must be set, even if empty)"
echo "  VLLM_MODEL=${VLLM_MODEL}"
echo "  VLLM_ARGS=${VLLM_ARGS:-<empty>}"

# ── Helper: check if PORTAL_CONFIG has an entry whose label matches ──

portal_config_has() {
    local pattern="$1"
    [[ -n "${PORTAL_CONFIG:-}" ]] && echo "$PORTAL_CONFIG" | tr '|' '\n' | grep -qiE "$pattern"
}

# ── Ray service ─────────────────────────────────────────────────────

echo ""
echo "  -- ray --"

if [[ -f /etc/supervisor/conf.d/ray.conf ]]; then
    assert_service_running ray
    echo "  ray: supervisor service running"

    # Verify gcs_server process is alive (vllm.sh waits for this)
    if pgrep -f gcs_server &>/dev/null; then
        echo "  ray: gcs_server process present"
    else
        fail_later "ray-gcs" "ray service running but gcs_server process not found"
    fi

    # Ray dashboard (informational — not critical for serving)
    ray_dash_port="${RAY_DASHBOARD_PORT:-28265}"
    if wait_for_port "$ray_dash_port" 10; then
        echo "  ray: dashboard listening on port ${ray_dash_port}"
    else
        echo "  WARN: ray dashboard not listening on port ${ray_dash_port}"
    fi
else
    echo "  skip: ray (no supervisor conf — external Ray cluster?)"
fi

# ── vLLM service ────────────────────────────────────────────────────

echo ""
echo "  -- vllm --"

assert_service_running vllm
echo "  vllm: supervisor service running"

# Check for early fatal errors in the log before waiting for the API.
# vLLM logs to /var/log/vllm.log (clean, ANSI-stripped).
if [[ -f "$VLLM_LOG" ]]; then
    # Look for Python tracebacks or known fatal patterns in the first
    # portion of the log (before model loading begins).
    fatal_patterns="Traceback|CUDA out of memory|RuntimeError:|OSError:|ValueError:|Failed to|Cannot find"
    early_errors=$(head -100 "$VLLM_LOG" | grep -cE "$fatal_patterns" || true)
    if [[ "$early_errors" -gt 0 ]]; then
        echo "  WARN: ${early_errors} potential error(s) in early vLLM log — may be transient"
        head -100 "$VLLM_LOG" | grep -E "$fatal_patterns" | head -5 | sed 's/^/    /'
    fi
fi

# ── Wait for vLLM API health ────────────────────────────────────────

echo ""
echo "  -- api health --"

HEALTH_URL="http://127.0.0.1:${VLLM_INTERNAL_PORT}/health"
elapsed=0
healthy=false
last_report=0
restart_count=0
last_pid=""

# Get initial vLLM PID from supervisor
last_pid=$(supervisorctl status vllm 2>/dev/null | grep -oP 'pid \K[0-9]+' || true)

_dump_log_tail() {
    if [[ -f "$VLLM_LOG" ]]; then
        echo "  last 10 lines of vllm log:"
        tail -10 "$VLLM_LOG" | sed 's/^/    /'
    fi
}

while (( elapsed < HEALTH_TIMEOUT )); do
    status=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null)
    if [[ "$status" == "200" ]]; then
        healthy=true
        break
    fi

    # Track supervisor restarts by PID change
    sup_line=$(supervisorctl status vllm 2>/dev/null)
    vllm_sup_state=$(echo "$sup_line" | awk '{print $2}')
    cur_pid=$(echo "$sup_line" | grep -oP 'pid \K[0-9]+' || true)

    if [[ "$vllm_sup_state" == "FATAL" ]]; then
        echo "  vllm entered FATAL state — supervisor gave up restarting"
        _dump_log_tail
        test_fail "vllm service FATAL (supervisor stopped restarting)"
    fi

    if [[ "$vllm_sup_state" != "RUNNING" && "$vllm_sup_state" != "STARTING" ]]; then
        echo "  vllm service in state: ${vllm_sup_state}"
        _dump_log_tail
        test_fail "vllm service not running (state: ${vllm_sup_state})"
    fi

    # Detect restart: PID changed while we were waiting
    if [[ -n "$last_pid" && -n "$cur_pid" && "$cur_pid" != "$last_pid" ]]; then
        restart_count=$((restart_count + 1))
        echo "  vllm restarted (pid ${last_pid} → ${cur_pid}, restart #${restart_count})"
        _dump_log_tail
        if (( restart_count >= MAX_RESTARTS )); then
            test_fail "vllm crash loop: ${restart_count} restarts detected (bad config or OOM?)"
        fi
    fi
    last_pid="$cur_pid"

    # Progress report every 30s
    if (( elapsed - last_report >= 30 )); then
        last_report=$elapsed
        log_size=$(stat -c '%s' "$VLLM_LOG" 2>/dev/null || echo "0")
        echo "  [${elapsed}s] waiting for /health (log=${log_size}B, http=${status}, restarts=${restart_count})"
    fi

    sleep 5
    elapsed=$((elapsed + 5))
done

if ! $healthy; then
    echo "  vLLM API did not become healthy within ${HEALTH_TIMEOUT}s"
    if [[ -f "$VLLM_LOG" ]]; then
        echo "  last 15 lines of vllm log:"
        tail -15 "$VLLM_LOG" | sed 's/^/    /'
    fi
    test_fail "vLLM API /health not reachable after ${HEALTH_TIMEOUT}s"
fi

echo "  vLLM API healthy after ${elapsed}s (${restart_count} restart(s))"

# ── Verify /v1/models ───────────────────────────────────────────────

echo ""
echo "  -- model verification --"

models_json=$(curl -sf --max-time 10 "http://127.0.0.1:${VLLM_INTERNAL_PORT}/v1/models" 2>/dev/null)
if [[ -z "$models_json" ]]; then
    fail_later "v1-models" "/v1/models returned empty response"
else
    # Parse model list, print details, and check if VLLM_MODEL is present
    model_info=$(echo "$models_json" | python3 -c "
import sys, json, os
d = json.load(sys.stdin)
models = d.get('data', [])
print(len(models))
for m in models:
    print(m.get('id', '?'))
# Check if VLLM_MODEL appears in model IDs
want = os.environ.get('VLLM_MODEL', '')
ids = [m.get('id', '') for m in models]
found = any(want in mid or mid in want for mid in ids)
print('found' if found else 'missing')
" 2>/dev/null)

    # First line: count, middle lines: model IDs, last line: found/missing
    count=$(echo "$model_info" | head -1)
    match=$(echo "$model_info" | tail -1)

    if [[ "${count:-0}" -gt 0 ]]; then
        echo "  /v1/models: ${count} model(s) loaded"
        echo "$model_info" | sed '1d;$d' | sed 's/^/    /'
    else
        fail_later "v1-models-empty" "/v1/models returned 0 models"
    fi

    if [[ "$match" == "found" ]]; then
        echo "  VLLM_MODEL found in /v1/models"
    else
        echo "  WARN: VLLM_MODEL '${VLLM_MODEL}' not found verbatim in /v1/models (may use a different ID)"
    fi
fi

# ── Log error check ─────────────────────────────────────────────────

echo ""
echo "  -- log analysis --"

# check_log_errors LABEL PATH [exclude_pattern]
# Grep for ERROR/CRITICAL lines in a log file, fail_later if any found.
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

if [[ -f "$VLLM_LOG" ]]; then
    log_size=$(stat -c '%s' "$VLLM_LOG" 2>/dev/null || echo "0")
    echo "  vllm log: ${log_size}B"
fi

check_log_errors "vllm" "$VLLM_LOG" "torch\.distributed|CUDAGraph|deprecat"
check_log_errors "ray" "$RAY_LOG"

# ── Port exposure check ─────────────────────────────────────────────
# Verify that services configured in PORTAL_CONFIG have their ports listening.
# This is especially important for serverless where caddy doesn't proxy —
# the services must bind directly.

echo ""
echo "  -- port exposure --"

declare -A PORT_CHECKS=(
    ["vllm"]="8000:vLLM API:vllm"
    ["model.ui"]="7860:Model UI:model.ui"
    ["ray"]="8265:Ray Dashboard:ray"
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

# ── Model UI (optional) ─────────────────────────────────────────────

if [[ -f /etc/supervisor/conf.d/model-ui.conf ]] && ! is_serverless; then
    echo ""
    echo "  -- model-ui --"
    model_ui_port=7860
    model_ui_state=$(supervisorctl status model-ui 2>/dev/null | awk '{print $2}')
    if [[ "$model_ui_state" == "RUNNING" ]]; then
        echo "  model-ui: RUNNING"
        if wait_for_port "$model_ui_port" 10; then
            echo "  model-ui: listening on port ${model_ui_port}"
            body=$(curl -sf --max-time 5 "http://127.0.0.1:${model_ui_port}/" 2>/dev/null)
            if [[ -n "$body" ]]; then
                echo "  model-ui: serves content"
            else
                echo "  WARN: model-ui port open but / returned empty"
            fi
        else
            echo "  WARN: model-ui running but port ${model_ui_port} not listening"
        fi
    elif [[ -z "${MODEL_NAME:-}" ]]; then
        echo "  model-ui: correctly not running (MODEL_NAME not set)"
    else
        echo "  WARN: model-ui state: ${model_ui_state:-unknown}"
    fi
fi

# ── Inference check ───────────────────────────────────────────────────
# VLLM_TEST_ENDPOINT controls inference testing:
#   "chat"  (default) — test via /v1/chat/completions
#   "none"  — skip inference test (for embedding/reranker models)
# Future: additional endpoints (e.g. "completions", "embeddings") can be
# added here as new branches.

echo ""
echo "  -- inference --"

VLLM_TEST_ENDPOINT="${VLLM_TEST_ENDPOINT:-chat}"
case "$VLLM_TEST_ENDPOINT" in
    chat|none) ;;
    *) test_fail "unsupported VLLM_TEST_ENDPOINT='${VLLM_TEST_ENDPOINT}' (must be 'chat' or 'none')" ;;
esac
VLLM_API="http://127.0.0.1:${VLLM_INTERNAL_PORT}"
# Use the model ID that vLLM is actually serving (from /v1/models)
SERVED_MODEL=$(curl -sf --max-time 10 "${VLLM_API}/v1/models" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null)
SERVED_MODEL="${SERVED_MODEL:-${VLLM_MODEL}}"

if [[ "${VLLM_TEST_ENDPOINT}" == "none" ]]; then
    echo "  inference test skipped (VLLM_TEST_ENDPOINT=none)"
else
    echo "  endpoint: ${VLLM_TEST_ENDPOINT} (/v1/chat/completions)"
    echo "  model: ${SERVED_MODEL}"

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

        # Capture body and HTTP status separately so we can diagnose non-2xx.
        http_body=$(mktemp)
        http_code=$(curl -s --max-time 60 -o "$http_body" -w '%{http_code}' \
            "${VLLM_API}/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d "$(python3 -c "
import json, sys
print(json.dumps({
    'model': sys.argv[1],
    'messages': [{'role': 'user', 'content': sys.argv[2]}],
    'temperature': 0.1
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

        # Parse response: extract token counts, finish reason, any content.
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

        # Inference ran if the model produced any completion tokens — even
        # if content is empty (reasoning model truncated, filtered, etc.).
        if (( ${compl_tokens:-0} > 0 )); then
            if [[ -n "$content" ]]; then
                display="${content:0:120}"
                [[ ${#content} -gt 120 ]] && display="${display}..."
                [[ "$content_kind" == "reasoning" ]] && echo "    (reasoning_content only)"
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

# ── Report ──────────────────────────────────────────────────────────

report_failures
test_pass "vLLM serving pipeline verified (model: ${VLLM_MODEL}, health: ${elapsed}s)"
