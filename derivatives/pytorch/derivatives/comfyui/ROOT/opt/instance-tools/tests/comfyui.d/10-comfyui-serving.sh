#!/bin/bash
# Test: ComfyUI serving pipeline — ComfyUI, the API wrapper, and workflow execution.
#
# api-wrapper.sh runs convert-workflows.sh at startup, which converts every
# GUI-format workflow from PROVISIONING_COMFYUI_WORKFLOWS into API-format
# payloads (wrapped in the {input: {workflow_json: ...}} request envelope) and
# writes them to /opt/comfyui-api-wrapper/payloads/. Any baked-in default
# payloads land there too.
#
# This test waits for ComfyUI and the API wrapper to come up, then POSTs every
# payload to the wrapper's /generate/sync endpoint. A payload passes if the
# wrapper returns a completed Result carrying at least one output. If every
# payload produces a result, the test passes.
#
# For each generated file the test prints a one-line metadata summary
# (filename, type, mimetype, size, and any S3 url) — the file bytes stay on
# the instance.
#
# TEST_TIMEOUT sizing: HEALTH_TIMEOUT (1800s) + N × GENERATE_TIMEOUT (1800s).
# 14400s covers boot-time health plus up to ~7 worst-case payloads at the
# full per-payload cap, comfortably above any realistic
# PROVISIONING_COMFYUI_WORKFLOWS count. Templates with very long video
# workflows can override COMFYUI_GENERATE_TIMEOUT; templates with many
# workflows should also override to keep N × GENERATE_TIMEOUT in budget.
# TEST_TIMEOUT=14400
source "$(dirname "$0")/../lib.sh"

# ── Constants ─────────────────────────────────────────────────────────
COMFYUI_PORT="${COMFYUI_PORT:-18188}"           # ComfyUI listens here (localhost)
API_WRAPPER_PORT="${API_WRAPPER_PORT:-18288}"   # API wrapper (uvicorn) listens here
PAYLOADS_DIR="/opt/comfyui-api-wrapper/payloads"
HEALTH_TIMEOUT="${COMFYUI_HEALTH_TIMEOUT:-1800}"      # ComfyUI + wrapper readiness
GENERATE_TIMEOUT="${COMFYUI_GENERATE_TIMEOUT:-1800}"  # per-workflow generation cap
MAX_RESTARTS=2                                        # fail after this many restarts (crash loop)

COMFYUI_URL="http://127.0.0.1:${COMFYUI_PORT}"
API_URL="http://127.0.0.1:${API_WRAPPER_PORT}"

# ── Skip if this isn't a GPU box ─────────────────────────────────────
# ComfyUI generation needs a GPU; there is nothing to exercise without one.

has_gpu || test_skip "no GPU detected — ComfyUI workflow execution requires a GPU"

# ── Diagnostics helper ───────────────────────────────────────────────

_dump_log() {
    local svc="$1"
    # logging.sh in /opt/supervisor-scripts/utils tees every supervised
    # service's stdout to /var/log/<PROC_NAME>.log (the clean, ANSI-stripped
    # log; matches the convention used by the vllm and llama serving tests).
    # supervisorctl tail is unreliable here because the supervisor confs set
    # stdout_logfile=/dev/stdout, which is not a regular log file.
    local log="/var/log/${svc}.log"
    if [[ -f "$log" ]]; then
        echo "  last 20 lines of ${log}:"
        tail -20 "$log" | sed 's/^/    /'
    else
        echo "  (no log file at ${log})"
    fi
}

# ── Wait for a supervisor service to become HTTP-healthy ─────────────
# Tracks supervisor state + PID so a FATAL service or a crash loop fails
# fast instead of waiting out the full HEALTH_TIMEOUT.

wait_for_service_health() {
    local svc="$1" url="$2" label="$3"
    local elapsed=0 last_report=0 restart_count=0 last_pid cur_pid sup_line state status

    last_pid=$(supervisorctl status "$svc" 2>/dev/null | grep -oP 'pid \K[0-9]+' || true)

    while (( elapsed < HEALTH_TIMEOUT )); do
        status=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null)
        if [[ "$status" == "200" ]]; then
            echo "  ${label}: healthy after ${elapsed}s (${restart_count} restart(s))"
            return 0
        fi

        sup_line=$(supervisorctl status "$svc" 2>/dev/null)
        state=$(echo "$sup_line" | awk '{print $2}')
        cur_pid=$(echo "$sup_line" | grep -oP 'pid \K[0-9]+' || true)

        # Supervisor state lifecycle: STOPPED → STARTING → RUNNING; on crash
        # → BACKOFF (transient, waiting to retry) → STARTING. RUNNING /
        # STARTING / BACKOFF / STOPPING are normal in-flight states — the
        # restart counter below catches crash loops via PID changes, and
        # HEALTH_TIMEOUT bounds the overall wait. Only FATAL (supervisor
        # gave up) and EXITED / STOPPED (autorestart not retrying) warrant
        # an immediate fail.
        case "$state" in
            RUNNING|STARTING|BACKOFF|STOPPING)
                ;;
            FATAL)
                echo "  ${svc} entered FATAL state — supervisor gave up restarting"
                _dump_log "$svc"
                test_fail "${svc} service FATAL (supervisor stopped restarting)"
                ;;
            EXITED|STOPPED)
                echo "  ${svc} unexpectedly ${state} (no longer serving)"
                _dump_log "$svc"
                test_fail "${svc} service ${state} (no autorestart pending)"
                ;;
            *)
                echo "  ${svc} in state: ${state:-unknown}"
                ;;
        esac

        if [[ -n "$last_pid" && -n "$cur_pid" && "$cur_pid" != "$last_pid" ]]; then
            restart_count=$((restart_count + 1))
            echo "  ${svc} restarted (pid ${last_pid} → ${cur_pid}, restart #${restart_count})"
            _dump_log "$svc"
            if (( restart_count >= MAX_RESTARTS )); then
                test_fail "${svc} crash loop: ${restart_count} restarts detected (bad config or OOM?)"
            fi
        fi
        # Only overwrite last_pid when we actually saw a pid. Transient
        # supervisor states (BACKOFF, STARTING between restarts) drop
        # the pid back to empty, and clobbering last_pid then would lose
        # the previous live pid — the next RUNNING(new-pid) would slip
        # past the restart comparison.
        [[ -n "$cur_pid" ]] && last_pid="$cur_pid"

        if (( elapsed - last_report >= 30 )); then
            last_report=$elapsed
            echo "  [${elapsed}s] waiting for ${label} (${url}, http=${status}, restarts=${restart_count})"
        fi

        sleep 5
        elapsed=$((elapsed + 5))
    done

    echo "  ${label} did not become healthy within ${HEALTH_TIMEOUT}s"
    _dump_log "$svc"
    test_fail "${label} not healthy after ${HEALTH_TIMEOUT}s"
}

# ── ComfyUI ──────────────────────────────────────────────────────────

echo ""
echo "  -- comfyui --"
wait_for_service_health comfyui "${COMFYUI_URL}/api/system_stats" "ComfyUI"

# ── API wrapper ──────────────────────────────────────────────────────
# /health returns 200 only once the wrapper can reach ComfyUI over HTTP+WS,
# so this also confirms the wrapper's view of ComfyUI is healthy.

echo ""
echo "  -- api wrapper --"
wait_for_service_health api-wrapper "${API_URL}/health" "API wrapper"

# ── Discover workflow payloads ───────────────────────────────────────

echo ""
echo "  -- workflow payloads --"

if [[ ! -d "$PAYLOADS_DIR" ]]; then
    test_skip "payloads directory ${PAYLOADS_DIR} does not exist (no API workflows configured)"
fi

shopt -s nullglob
payloads=("${PAYLOADS_DIR}"/*.json)
shopt -u nullglob

if [[ ${#payloads[@]} -eq 0 ]]; then
    test_skip "no API-format workflows in ${PAYLOADS_DIR} (nothing to exercise)"
fi

echo "  ${#payloads[@]} payload(s) found in ${PAYLOADS_DIR}"

# ── Exercise each workflow through /generate/sync ────────────────────

pass=0
fail=0

for payload in "${payloads[@]}"; do
    name=$(basename "$payload")
    echo ""
    echo "  -- workflow: ${name} --"

    # Reject a malformed payload before posting — a JSON parse error here
    # is a conversion bug, not a generation failure.
    if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$payload" 2>/dev/null; then
        echo "    FAIL: ${name} is not valid JSON"
        fail=$((fail + 1))
        continue
    fi

    body=$(mktemp)
    code=$(curl -s --max-time "$GENERATE_TIMEOUT" -o "$body" -w '%{http_code}' \
        -X POST "${API_URL}/generate/sync" \
        -H 'Content-Type: application/json' \
        --data-binary @"$payload" 2>/dev/null)

    # Parse the Result and print a one-line metadata summary per output.
    # The printer exits 0 when the workflow produced at least one output,
    # 1 otherwise. The response body is passed as a file path (argv[2])
    # rather than on stdin to keep the parsing self-contained.
    if python3 -c "
import sys, json, os, mimetypes

code = sys.argv[1]
try:
    with open(sys.argv[2], encoding='utf-8') as fh:
        d = json.load(fh)
except Exception as e:
    print(f'    FAIL: malformed response (HTTP {code}): {e}')
    sys.exit(1)

def human(n):
    n = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024

status  = d.get('status', '?')
message = d.get('message') or ''
outputs = d.get('output') or []
timings = d.get('timings') or {}
gen_ms  = timings.get('generation_ms', '?')
total_ms = timings.get('total_ms', '?')

if code != '200' or status != 'completed':
    print(f'    FAIL: HTTP {code}, status={status}')
    if message:
        print(f'    message: {message[:300]}')
    sys.exit(1)

if not outputs:
    print('    FAIL: status completed but produced 0 outputs')
    sys.exit(1)

print(f'    completed: {len(outputs)} output(s)  generation={gen_ms}ms  total={total_ms}ms')
for i, o in enumerate(outputs, 1):
    fn   = o.get('filename', '?')
    typ  = o.get('type', '?')
    path = o.get('local_path')
    # mimetype is only on the Result when base64 inlining ran; otherwise
    # guess from the extension. Size comes straight off the file on disk.
    mime = o.get('mimetype') or mimetypes.guess_type(fn)[0] or '?'
    try:
        size = human(os.path.getsize(path)) if path else '?'
    except OSError:
        size = '?'
    line = f'    [{i}] {fn}  type={typ}  mime={mime}  size={size}'
    if o.get('url'):
        line += f\"  url={o['url']}\"
    print(line)
sys.exit(0)
" "$code" "$body"; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
    fi
    rm -f "$body"
done

# ── Report ───────────────────────────────────────────────────────────

echo ""
echo "  workflows: ${pass}/${#payloads[@]} produced output, ${fail} failed"

if (( fail > 0 )); then
    fail_later "workflows" "${fail}/${#payloads[@]} workflow(s) did not produce a result"
fi

report_failures
test_pass "ComfyUI serving pipeline verified (${pass}/${#payloads[@]} workflows produced output)"
