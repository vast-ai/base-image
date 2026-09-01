#!/bin/bash
# Test: functional validation of running services.
# Only tests services that are actually running — skips gracefully otherwise.
# Runs after 65-conditional-services.sh (which validates state).
source "$(dirname "$0")/../lib.sh"

# FAILURES and fail_later/report_failures come from lib.sh
# service_running comes from lib.sh

# ── Instance Portal ──────────────────────────────────────────────────

# EXPECTED = configured AND routed AND not-serverless. The .conf alone is not enough and
# assuming it was would have broken the build: syncthing.conf and tensorboard.conf ship
# UNCONDITIONALLY in base, but 5 of the 7 QA templates carry no portal entry for them, so
# exit_portal.sh correctly exits 0 and the service sits EXITED by design. Requiring the
# port there means a 60s wait and a hard fail on a healthy image — and runner.sh skips the
# entire derivative phase on any base/* failure, so it would have reddened every
# derivative QA cell. base-qa and pytorch-qa list every entry, which is exactly why this
# looked safe when only base was considered.
#
# `! is_serverless` is the other half: base/85-serverless-services asserts these SAME
# programs are STOPPED in serverless mode. Without it two base tests assert opposite
# verdicts on one instance. 65-conditional-services already uses this exact predicate.
if [[ -f /etc/supervisor/conf.d/instance_portal.conf ]] && portal_has_entry "instance portal" && ! is_serverless; then
    assert_service_serving instance_portal 11111
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
    echo "  skip: instance_portal (not configured, not routed, or serverless)"
fi

# ── Tunnel Manager ───────────────────────────────────────────────────

# CONFIGURED is decided from the supervisor conf, never from the status word: every
# failure mode also produces a non-RUNNING word, so `if service_running x` collapsed
# "not configured", "RUNNING but never bound", and "supervisord never heard of it" into
# one silent skip that let test_pass fire. A hung service — RUNNING, binding nothing —
# was reported as ALL TESTS PASSED. assert_service_serving fails on either half.
if [[ -f /etc/supervisor/conf.d/tunnel_manager.conf ]] && portal_has_entry "tunnel" && ! is_serverless; then
    assert_service_serving tunnel_manager 11112
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
    echo "  skip: tunnel_manager (not configured, not routed, or serverless)"
fi

# ── TensorBoard ──────────────────────────────────────────────────────

if [[ -f /etc/supervisor/conf.d/tensorboard.conf ]] && portal_has_entry "tensorboard" && ! is_serverless; then
    assert_service_serving tensorboard 16006
    echo "  -- tensorboard --"

    # Root page returns HTML
    body=$(curl -sf --max-time 5 http://127.0.0.1:16006/ 2>/dev/null)
    if [[ -n "$body" ]]; then
        echo "  tensorboard: / returns content"
    else
        fail_later "tensorboard" "/ returned empty response"
    fi
else
    echo "  skip: tensorboard (not configured, not routed, or serverless)"
fi

# ── Syncthing ────────────────────────────────────────────────────────

if [[ -f /etc/supervisor/conf.d/syncthing.conf ]] && portal_has_entry "syncthing" && ! is_serverless; then
    assert_service_serving syncthing 18384
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
    echo "  skip: syncthing (not configured, not routed, or serverless)"
fi

# ── Jupyter ──────────────────────────────────────────────────────────

# Env-overridable like the readiness budgets in lib.sh: this suite ships INSIDE the
# image, so a baked-in wrong number can only be corrected by rebuilding and
# re-promoting, while behind a variable it is a template edit (ADR 0029).
JUPYTER_READY_TIMEOUT="${JUPYTER_READY_TIMEOUT:-60}"

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

    # Root page — follows redirects (jupyter redirects / → /lab or /tree).
    #
    # BOUNDED WAIT, not a single probe, because presence is not readiness (ADR 0029).
    # The caller's gate is `pgrep` plus a bound port, and both are satisfied the
    # instant jupyter starts listening — which is before it serves. A single 5s curl
    # then races startup.
    #
    # Measured on the first serverless cell ever run: base/65 found NO jupyter
    # process, and base/67 one second later found the port bound and `/` empty, so
    # the whole cell failed and ADR 0030's phase gate then refused to start the
    # derivative phase. The standard cells passed the identical check on the
    # identical template, because there jupyter had been up since boot and answered
    # first time. Same image, same template — the only variable was WHEN jupyter came
    # up, which is the definition of a race rather than a defect.
    local deadline=$(( SECONDS + JUPYTER_READY_TIMEOUT ))
    local body=""
    while :; do
        body=$(curl -s $curl_opts "${base_url}/${token_param}" 2>/dev/null)
        [[ -n "$body" ]] && break
        (( SECONDS >= deadline )) && break
        sleep 2
    done
    if [[ -n "$body" ]]; then
        echo "  jupyter (${label}): / returns content"
    else
        # Name the process holding the port. "empty response" alone cannot
        # distinguish jupyter-not-ready from something-else-is-bound, and that
        # ambiguity cost a full cell to resolve from logs after the fact.
        local owner
        owner=$(listener_owner "${port}")
        echo "  listener on ${port}: ${owner:-<none>}"
        fail_later "jupyter" "/ returned empty response on port ${port} after ${JUPYTER_READY_TIMEOUT}s"
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
# Same rule as the services above: presence of the supervisor conf decides whether jupyter
# is EXPECTED here, and once expected, RUNNING-but-unbound is a failure rather than a skip.
# `.launch`-managed jupyter (checked above) legitimately means no supervisor jupyter.
# `.launch`-managed jupyter means the SUPERVISOR unit must be EXITED — 65 asserts exactly
# that. Detect who manages it the way 65 does, rather than inferring it from whether the
# probe above happened to win a startup race this file's own comments document losing.
launch_manages_jupyter=false
if [[ -f /.launch ]] && grep -qi jupyter /.launch && [[ "${JUPYTER_OVERRIDE,,}" != "true" ]]; then
    launch_manages_jupyter=true
fi
if ! $launch_manages_jupyter && ! is_serverless \
   && [[ -f /etc/supervisor/conf.d/jupyter.conf ]] && portal_has_entry "jupyter"; then
    echo "  -- jupyter (supervisor-managed, port 18080) --"
    assert_service_serving jupyter 18080 "${JUPYTER_READY_TIMEOUT:-60}"
    check_jupyter_functional 18080 "supervisor"
    jupyter_tested=true
fi

if ! $jupyter_tested; then
    echo "  skip: jupyter (.launch-managed, serverless, or not routed)"
fi

# ── syncthing: the configured listener must be usable, not just present ──
#
# A linter can prove the SOURCE is guarded (L068); only a live boot can prove what
# ended up in config.xml. That distinction matters here because the malformed value
# is persisted on overlayfs, which survives stop/start — an instance that booted
# once with the old script keeps the bad entry even after the image is fixed.
#
# What went wrong: `tcp://0.0.0.0:${VAST_TCP_PORT_72299}` with the var unset is a
# valid address with an empty port, so syncthing bound its own default [::]:22000 —
# a port nothing publishes. Inbound direct connections were impossible and sync fell
# back to relay-only (slow, rate-limited), which defeats the point of syncthing.
# Nothing failed; it just quietly did not work. (ADR 0028)
if service_running syncthing; then
    echo "  -- syncthing listen addresses --"
    _st_conf="${STCONFDIR:-/opt/syncthing/config}/config.xml"
    if [[ -r "$_st_conf" ]]; then
        if grep -qE '<listenAddress>(tcp|quic)://[^<]*:</listenAddress>' "$_st_conf"; then
            fail_later "syncthing-empty-port" \
                       "config.xml has a listen address with an EMPTY port — syncthing resolves that to its own default (22000), which no template publishes, so direct sync cannot work (ADR 0028)"
        else
            echo "     ok: no empty-port listen address"
        fi
        # If the platform mapped the sync port, that exact address must be configured
        # — otherwise the direct listener the mapping exists for is simply absent.
        if [[ "${VAST_TCP_PORT_72299:-}" =~ ^[0-9]+$ ]]; then
            if grep -qF "tcp://0.0.0.0:${VAST_TCP_PORT_72299}<" "$_st_conf"; then
                echo "     ok: direct listener on mapped port ${VAST_TCP_PORT_72299}"
            else
                fail_later "syncthing-mapped-port" \
                           "VAST_TCP_PORT_72299=${VAST_TCP_PORT_72299} is mapped but syncthing has no matching listen address — direct peer connections will not work and sync stays relay-only"
            fi
        else
            echo "     ok: port unmapped, relay-only by design"
        fi
    else
        echo "     skip: $_st_conf not readable"
    fi
fi

# ── Report ───────────────────────────────────────────────────────────

report_failures

test_pass "running services respond correctly"
