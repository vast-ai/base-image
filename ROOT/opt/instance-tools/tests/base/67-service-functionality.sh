#!/bin/bash
# Test: functional validation of running services.
# Only tests services that are actually running — skips gracefully otherwise.
# Runs after 65-conditional-services.sh (which validates state).
source "$(dirname "$0")/../lib.sh"

FAILURES=()
fail_later() {
    FAILURES+=("$1")
    echo "  FAIL: $1: $2"
}

service_running() {
    local name="$1"
    local status
    status=$(supervisorctl status "$name" 2>/dev/null | awk '{print $2}')
    [[ "$status" == "RUNNING" ]]
}

# ── Instance Portal ──────────────────────────────────────────────────

if service_running instance_portal && wait_for_port 11111 5; then
    echo "  -- instance_portal --"

    # HTML UI
    body=$(curl -sf --max-time 5 http://127.0.0.1:11111/ 2>/dev/null)
    if [[ -n "$body" ]] && echo "$body" | grep -qi "<html"; then
        echo "  portal: serves HTML"
    else
        fail_later "portal" "/ did not return HTML"
    fi

    # /get-applications returns valid JSON
    apps=$(curl -sf --max-time 5 http://127.0.0.1:11111/get-applications 2>/dev/null)
    if echo "$apps" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        echo "  portal: /get-applications returns valid JSON"
    else
        fail_later "portal" "/get-applications did not return valid JSON"
    fi

    # /system-metrics returns JSON with expected keys
    metrics=$(curl -sf --max-time 5 http://127.0.0.1:11111/system-metrics 2>/dev/null)
    if echo "$metrics" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert any(k in d for k in ('cpu', 'gpu', 'memory', 'disk'))
" 2>/dev/null; then
        echo "  portal: /system-metrics returns metrics"
    else
        echo "  WARN: /system-metrics did not return expected data"
    fi

    # /supervisor/processes returns JSON list
    procs=$(curl -sf --max-time 5 http://127.0.0.1:11111/supervisor/processes 2>/dev/null)
    if echo "$procs" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d,list)" 2>/dev/null; then
        count=$(echo "$procs" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
        echo "  portal: /supervisor/processes lists ${count} processes"
    else
        echo "  WARN: /supervisor/processes did not return a JSON list"
    fi
else
    echo "  skip: instance_portal (not running)"
fi

# ── Tunnel Manager ───────────────────────────────────────────────────

if service_running tunnel_manager && wait_for_port 11112 5; then
    echo "  -- tunnel_manager --"

    # /get-all-quick-tunnels returns JSON array
    tunnels=$(curl -sf --max-time 5 http://127.0.0.1:11112/get-all-quick-tunnels 2>/dev/null)
    if echo "$tunnels" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d,list)" 2>/dev/null; then
        count=$(echo "$tunnels" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
        echo "  tunnel_manager: /get-all-quick-tunnels returns ${count} tunnels"
    else
        fail_later "tunnel_manager" "/get-all-quick-tunnels did not return a JSON array"
    fi
else
    echo "  skip: tunnel_manager (not running)"
fi

# ── TensorBoard ──────────────────────────────────────────────────────

if service_running tensorboard && wait_for_port 16006 5; then
    echo "  -- tensorboard --"

    # Root page returns HTML
    body=$(curl -sf --max-time 5 http://127.0.0.1:16006/ 2>/dev/null)
    if [[ -n "$body" ]]; then
        echo "  tensorboard: / returns content"
    else
        fail_later "tensorboard" "/ returned empty response"
    fi
else
    echo "  skip: tensorboard (not running)"
fi

# ── Syncthing ────────────────────────────────────────────────────────

if service_running syncthing && wait_for_port 18384 5; then
    echo "  -- syncthing --"

    # insecure-admin-access is enabled so no API key needed for local requests.
    # But the GUI may still require it — try without, then with OPEN_BUTTON_TOKEN.
    status_json=""
    for header in "" "X-API-Key: ${OPEN_BUTTON_TOKEN:-}"; do
        if [[ -n "$header" ]]; then
            status_json=$(curl -sf --max-time 5 -H "$header" http://127.0.0.1:18384/rest/system/status 2>/dev/null)
        else
            status_json=$(curl -sf --max-time 5 http://127.0.0.1:18384/rest/system/status 2>/dev/null)
        fi
        if echo "$status_json" | python3 -c "import sys,json; d=json.load(sys.stdin); d['myID']" 2>/dev/null; then
            break
        fi
        status_json=""
    done

    if [[ -n "$status_json" ]]; then
        version=$(echo "$status_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','?'))" 2>/dev/null)
        echo "  syncthing: /rest/system/status ok (version: ${version})"
    else
        fail_later "syncthing" "/rest/system/status did not return valid JSON"
    fi
else
    echo "  skip: syncthing (not running)"
fi

# ── Jupyter ──────────────────────────────────────────────────────────

check_jupyter_functional() {
    local port="$1"
    local label="$2"

    # Jupyter API — no auth required on supervisor-managed (token disabled)
    # .launch-managed uses TLS with self-signed certs and redirects / → /lab or /tree
    local proto="http"
    local curl_opts="--max-time 5 -L"
    if [[ "$port" == "8080" ]]; then
        proto="https"
        curl_opts="--max-time 5 -L -k"
    fi

    local base_url="${proto}://127.0.0.1:${port}"

    # Build token query param if available
    local token_param=""
    if [[ -n "${JUPYTER_TOKEN:-}" ]]; then
        token_param="?token=${JUPYTER_TOKEN}"
    fi

    # Root page — follows redirects (jupyter redirects / → /lab or /tree)
    body=$(curl -s $curl_opts "${base_url}/${token_param}" 2>/dev/null)
    if [[ -n "$body" ]]; then
        echo "  jupyter (${label}): / returns content"
    else
        fail_later "jupyter" "/ returned empty response on port ${port}"
        return
    fi

    # /api/kernelspecs — list available kernel specs
    kernelspecs=$(curl -s $curl_opts "${base_url}/api/kernelspecs${token_param}" 2>/dev/null)
    if echo "$kernelspecs" | python3 -c "import sys,json; d=json.load(sys.stdin); d['kernelspecs']" 2>/dev/null; then
        count=$(echo "$kernelspecs" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['kernelspecs']))")
        echo "  jupyter (${label}): ${count} kernel spec(s) available"
    else
        echo "  WARN: jupyter /api/kernelspecs not accessible"
    fi
}

jupyter_tested=false

# Check .launch-managed jupyter first (port 8080)
if [[ -f /.launch ]] && grep -qi jupyter /.launch && [[ "${JUPYTER_OVERRIDE,,}" != "true" ]]; then
    if pgrep -f "jupyter" &>/dev/null && ss -tln | grep -q ":8080 "; then
        echo "  -- jupyter (.launch-managed, port 8080) --"
        check_jupyter_functional 8080 ".launch"
        jupyter_tested=true
    fi
fi

# Check supervisor-managed jupyter (port 18080)
if ! $jupyter_tested && service_running jupyter && wait_for_port 18080 5; then
    echo "  -- jupyter (supervisor-managed, port 18080) --"
    check_jupyter_functional 18080 "supervisor"
    jupyter_tested=true
fi

if ! $jupyter_tested; then
    echo "  skip: jupyter (not running or not listening)"
fi

# ── Report ───────────────────────────────────────────────────────────

if [[ ${#FAILURES[@]} -gt 0 ]]; then
    joined=$(printf '%s, ' "${FAILURES[@]}")
    test_fail "${#FAILURES[@]} service(s) failed functional checks: ${joined%, }"
fi

test_pass "running services respond correctly"
