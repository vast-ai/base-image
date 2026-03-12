#!/bin/bash
# Test: Caddy authentication on external ports.
# Verifies bearer token, basic auth, query-param token, and 401 on unauthenticated requests.
source "$(dirname "$0")/../lib.sh"

is_serverless && test_skip "serverless (no caddy)"
[[ -f /etc/Caddyfile ]] || test_skip "no Caddyfile"

FAILURES=()
fail_later() {
    FAILURES+=("$1")
    echo "  FAIL: $1: $2"
}

# ── OPEN_BUTTON_TOKEN is required ────────────────────────────────────

[[ -n "${OPEN_BUTTON_TOKEN:-}" ]] || test_fail "OPEN_BUTTON_TOKEN is not set"
echo "  OPEN_BUTTON_TOKEN present"

# ── AUTH_EXCLUDE — ports that bypass auth ─────────────────────────────

# Parse comma-delimited list into an associative array for fast lookup
declare -A EXCLUDED_PORTS=()
if [[ -n "${AUTH_EXCLUDE:-}" ]]; then
    echo "  AUTH_EXCLUDE=${AUTH_EXCLUDE}"
    IFS=',' read -ra _exc <<< "$AUTH_EXCLUDE"
    for p in "${_exc[@]}"; do
        p=$(echo "$p" | tr -d ' ')
        [[ -n "$p" ]] && EXCLUDED_PORTS[$p]=1
    done
fi

is_excluded() {
    [[ -n "${EXCLUDED_PORTS[$1]:-}" ]]
}

# ── WEB_USERNAME / WEB_PASSWORD — set if absent, restart caddy ───────

auth_modified=false
if [[ -z "${WEB_PASSWORD:-}" ]]; then
    echo "  WEB_PASSWORD not set — creating test credentials"
    test_web_user="${WEB_USERNAME:-testuser}"
    test_web_pass="testpass_$(openssl rand -hex 8)"

    # Write to /etc/environment so caddy picks them up on restart
    sed -i '/^WEB_USERNAME=/d' /etc/environment 2>/dev/null
    sed -i '/^WEB_PASSWORD=/d' /etc/environment 2>/dev/null
    echo "WEB_USERNAME=\"${test_web_user}\"" >> /etc/environment
    echo "WEB_PASSWORD=\"${test_web_pass}\"" >> /etc/environment

    # Export for this test
    export WEB_USERNAME="$test_web_user"
    export WEB_PASSWORD="$test_web_pass"

    # Restart caddy to regenerate Caddyfile with new credentials
    echo "  restarting caddy to pick up WEB_USERNAME/WEB_PASSWORD..."
    supervisorctl restart caddy &>/dev/null
    sleep 2
    auth_modified=true
else
    echo "  WEB_PASSWORD present"
    if [[ -n "${WEB_USERNAME:-}" ]]; then
        echo "  WEB_USERNAME=${WEB_USERNAME}"
    else
        echo "  WEB_USERNAME not set (default: vastai)"
    fi
fi

# ── Find an external port to test against ────────────────────────────
# External ports are those where Caddy proxies (external != internal).
# We need at least one to test auth.

# Collect all external caddy ports
all_caddy_ports=()
while IFS= read -r line; do
    port=$(echo "$line" | grep -oP ':\K[0-9]+(?= \{)')
    [[ -n "$port" && "$port" != "2019" ]] || continue
    ss -tln | grep -q ":${port} " && all_caddy_ports+=("$port")
done < <(grep -P '^:\d+ \{' /etc/Caddyfile)

# Pick a non-excluded port for main auth tests
test_port=""
for p in "${all_caddy_ports[@]}"; do
    if ! is_excluded "$p"; then
        test_port="$p"
        break
    fi
done

if [[ ${#all_caddy_ports[@]} -eq 0 ]]; then
    if $auth_modified; then
        sed -i '/^WEB_USERNAME=/d' /etc/environment
        sed -i '/^WEB_PASSWORD=/d' /etc/environment
        supervisorctl restart caddy &>/dev/null
    fi
    test_skip "no external caddy ports found"
fi

# Helper to detect protocol for a port
get_proto() {
    local p="$1"
    if grep -A5 "^:${p} {" /etc/Caddyfile | grep -q "tls "; then
        echo "https"
    else
        echo "http"
    fi
}

if [[ -z "$test_port" ]]; then
    echo "  all caddy ports are in AUTH_EXCLUDE — skipping auth enforcement tests"
else
    echo "  testing auth on port ${test_port}"

    proto=$(get_proto "$test_port")
    curl_base="curl -s --max-time 5"
    [[ "$proto" == "https" ]] && curl_base="$curl_base -k"
fi

# ── Tests 1–8 only run if we have a non-excluded port ────────────────

if [[ -n "$test_port" ]]; then

# ── Test 1: No auth → 401 ───────────────────────────────────────────

status=$($curl_base -o /dev/null -w '%{http_code}' "${proto}://127.0.0.1:${test_port}/" 2>/dev/null)
if [[ "$status" == "401" ]]; then
    echo "  no auth → 401 (correct)"
else
    fail_later "no-auth" "expected 401, got ${status}"
fi

# ── Test 2: Bearer OPEN_BUTTON_TOKEN → 200 ──────────────────────────

status=$($curl_base -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${OPEN_BUTTON_TOKEN}" \
    "${proto}://127.0.0.1:${test_port}/" 2>/dev/null)
if [[ "$status" == "200" || "$status" == "302" ]]; then
    echo "  bearer OPEN_BUTTON_TOKEN → ${status} (correct)"
else
    fail_later "bearer-obt" "expected 200/302, got ${status}"
fi

# ── Test 3: Bearer WEB_PASSWORD → 200 ───────────────────────────────

if [[ -n "${WEB_PASSWORD:-}" ]]; then
    status=$($curl_base -o /dev/null -w '%{http_code}' \
        -H "Authorization: Bearer ${WEB_PASSWORD}" \
        "${proto}://127.0.0.1:${test_port}/" 2>/dev/null)
    if [[ "$status" == "200" || "$status" == "302" ]]; then
        echo "  bearer WEB_PASSWORD → ${status} (correct)"
    else
        fail_later "bearer-webpass" "expected 200/302, got ${status}"
    fi
fi

# ── Test 4: Basic auth with username:WEB_PASSWORD → 200 ──────────────
# When WEB_USERNAME is not set, the config manager defaults to "vastai"

basic_user="${WEB_USERNAME:-vastai}"
status=$($curl_base -o /dev/null -w '%{http_code}' \
    -u "${basic_user}:${WEB_PASSWORD}" \
    "${proto}://127.0.0.1:${test_port}/" 2>/dev/null)
if [[ "$status" == "200" || "$status" == "302" ]]; then
    echo "  basic auth ${basic_user}:WEB_PASSWORD → ${status} (correct)"
else
    fail_later "basic-auth-webpass" "expected 200/302, got ${status}"
fi

# ── Test 5: Basic auth with username:OPEN_BUTTON_TOKEN ────────────────
# Basic auth password is bcrypt-hashed WEB_PASSWORD. OBT only works
# as a basic auth password when WEB_PASSWORD == OBT.
# Bearer accepts both tokens regardless.

if [[ "${WEB_PASSWORD}" == "${OPEN_BUTTON_TOKEN}" ]]; then
    echo "  skip: basic auth with OBT (same as WEB_PASSWORD, already tested)"
else
    status=$($curl_base -o /dev/null -w '%{http_code}' \
        -u "${basic_user}:${OPEN_BUTTON_TOKEN}" \
        "${proto}://127.0.0.1:${test_port}/" 2>/dev/null)
    if [[ "$status" == "401" ]]; then
        echo "  basic auth ${basic_user}:OPEN_BUTTON_TOKEN → 401 (correct, WEB_PASSWORD differs)"
    elif [[ "$status" == "200" || "$status" == "302" ]]; then
        echo "  WARN: basic auth ${basic_user}:OPEN_BUTTON_TOKEN → ${status} (unexpected)"
    else
        echo "  basic auth ${basic_user}:OPEN_BUTTON_TOKEN → ${status}"
    fi
fi

# ── Test 6: Query param token → 302 (redirect with cookie) ──────────

status=$($curl_base -o /dev/null -w '%{http_code}' \
    "${proto}://127.0.0.1:${test_port}/?token=${OPEN_BUTTON_TOKEN}" 2>/dev/null)
if [[ "$status" == "302" || "$status" == "200" ]]; then
    echo "  query param ?token=OBT → ${status} (correct)"
else
    fail_later "token-param" "expected 302/200, got ${status}"
fi

# ── Test 7: Wrong bearer token → 401 ────────────────────────────────

status=$($curl_base -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer wrong_token_value" \
    "${proto}://127.0.0.1:${test_port}/" 2>/dev/null)
if [[ "$status" == "401" ]]; then
    echo "  wrong bearer → 401 (correct)"
else
    fail_later "wrong-bearer" "expected 401, got ${status}"
fi

# ── Test 8: Wrong basic auth → 401 ──────────────────────────────────

status=$($curl_base -o /dev/null -w '%{http_code}' \
    -u "nobody:wrongpassword" \
    "${proto}://127.0.0.1:${test_port}/" 2>/dev/null)
if [[ "$status" == "401" ]]; then
    echo "  wrong basic auth → 401 (correct)"
else
    fail_later "wrong-basic" "expected 401, got ${status}"
fi

fi  # end of non-excluded port tests

# ── Test 9: Auth on additional ports ─────────────────────────────────
# Check that other external ports also enforce auth (or are excluded)

additional_tested=0
for port in "${all_caddy_ports[@]}"; do
    [[ "$port" == "$test_port" ]] && continue

    port_proto="http"
    if grep -A5 "^:${port} {" /etc/Caddyfile | grep -q "tls "; then
        port_proto="https"
    fi
    port_curl="curl -s --max-time 3"
    [[ "$port_proto" == "https" ]] && port_curl="$port_curl -k"

    s=$($port_curl -o /dev/null -w '%{http_code}' "${port_proto}://127.0.0.1:${port}/" 2>/dev/null)

    if is_excluded "$port"; then
        # Excluded port — should be reachable without auth
        if [[ "$s" == "200" || "$s" == "302" ]]; then
            echo "  port ${port}: ${s} without auth (AUTH_EXCLUDE, correct)"
        elif [[ "$s" == "502" ]]; then
            echo "  port ${port}: 502 (AUTH_EXCLUDE, backend down)"
        else
            fail_later "port-${port}-exclude" "AUTH_EXCLUDE but got ${s}, expected 200/302/502"
        fi
    else
        # Normal port — should require auth
        if [[ "$s" == "401" ]]; then
            echo "  port ${port}: 401 without auth (correct)"
        elif [[ "$s" == "502" ]]; then
            echo "  port ${port}: 502 (backend down, auth not testable)"
        else
            fail_later "port-${port}-noauth" "expected 401, got ${s}"
        fi
    fi
    additional_tested=$((additional_tested + 1))
done

if [[ $additional_tested -gt 0 ]]; then
    echo "  checked ${additional_tested} additional port(s)"
fi

# ── Test 10: AUTH_EXCLUDE active verification ────────────────────────
# If the template didn't set AUTH_EXCLUDE, we test it ourselves by
# temporarily excluding a port, verifying open access, then restoring.

if [[ -z "${AUTH_EXCLUDE:-}" && -n "$test_port" ]]; then
    echo "  AUTH_EXCLUDE not set — testing exclusion on port ${test_port}"
    sed -i '/^AUTH_EXCLUDE=/d' /etc/environment 2>/dev/null
    echo "AUTH_EXCLUDE=\"${test_port}\"" >> /etc/environment
    supervisorctl restart caddy &>/dev/null
    sleep 2

    exc_proto=$(get_proto "$test_port")
    exc_curl="curl -s --max-time 5"
    [[ "$exc_proto" == "https" ]] && exc_curl="$exc_curl -k"

    s=$($exc_curl -o /dev/null -w '%{http_code}' "${exc_proto}://127.0.0.1:${test_port}/" 2>/dev/null)
    if [[ "$s" == "200" || "$s" == "302" ]]; then
        echo "  AUTH_EXCLUDE=${test_port}: ${s} without auth (correct)"
    elif [[ "$s" == "502" ]]; then
        echo "  AUTH_EXCLUDE=${test_port}: 502 (backend down, exclusion not verifiable)"
    else
        fail_later "auth-exclude-test" "AUTH_EXCLUDE=${test_port} but got ${s}, expected 200/302"
    fi

    # Restore: remove AUTH_EXCLUDE and restart caddy
    sed -i '/^AUTH_EXCLUDE=/d' /etc/environment
    supervisorctl restart caddy &>/dev/null
    sleep 2
    echo "  AUTH_EXCLUDE test complete, restored"
fi

# ── Test 11: VAST_TCP_PORT / VAST_UDP_PORT listeners ─────────────────
# Verify that each port the platform has mapped actually has a listener.

echo "  -- external port mappings --"
port_check_count=0
while IFS='=' read -r varname mapped_port; do
    container_port="${varname#VAST_TCP_PORT_}"
    # Skip SSH — always managed by the platform
    [[ "$container_port" == "22" ]] && continue

    if ss -tln | grep -qE ":(${container_port}) "; then
        echo "  TCP ${container_port} (→${mapped_port}): listening"
    else
        fail_later "tcp-${container_port}" "VAST_TCP_PORT_${container_port} set but nothing listening on TCP ${container_port}"
    fi
    port_check_count=$((port_check_count + 1))
done < <(env | grep '^VAST_TCP_PORT_' | sort)

while IFS='=' read -r varname mapped_port; do
    [[ -n "$varname" ]] || continue
    container_port="${varname#VAST_UDP_PORT_}"

    if ss -uln | grep -qE ":(${container_port}) "; then
        echo "  UDP ${container_port} (→${mapped_port}): listening"
    else
        echo "  WARN: VAST_UDP_PORT_${container_port} set but nothing listening on UDP ${container_port}"
    fi
    port_check_count=$((port_check_count + 1))
done < <(env | grep '^VAST_UDP_PORT_' | sort)

# Syncthing special case: if running, it should have both TCP and UDP
# on its data sync port (VAST_TCP_PORT_72299)
if [[ -n "${VAST_TCP_PORT_72299:-}" ]]; then
    sync_port="${VAST_TCP_PORT_72299}"
    if supervisorctl status syncthing 2>/dev/null | grep -q RUNNING; then
        if ss -tln | grep -qE ":(${sync_port}) "; then
            echo "  syncthing TCP sync port ${sync_port}: listening"
        else
            echo "  WARN: syncthing running but TCP ${sync_port} not listening"
        fi
    fi
fi

echo "  checked ${port_check_count} port mapping(s)"

# ── Cleanup: remove test credentials if we created them ──────────────

if $auth_modified; then
    echo "  cleaning up test credentials and restarting caddy..."
    sed -i '/^WEB_USERNAME=/d' /etc/environment
    sed -i '/^WEB_PASSWORD=/d' /etc/environment
    supervisorctl restart caddy &>/dev/null
    sleep 2
fi

# ── Report ───────────────────────────────────────────────────────────

if [[ ${#FAILURES[@]} -gt 0 ]]; then
    joined=$(printf '%s, ' "${FAILURES[@]}")
    test_fail "${#FAILURES[@]} auth check(s) failed: ${joined%, }"
fi

excluded_count=${#EXCLUDED_PORTS[@]}
if [[ $excluded_count -gt 0 ]]; then
    test_pass "caddy auth verified (test port: ${test_port:-none}, ${excluded_count} excluded)"
else
    test_pass "caddy auth verified on port ${test_port}"
fi
