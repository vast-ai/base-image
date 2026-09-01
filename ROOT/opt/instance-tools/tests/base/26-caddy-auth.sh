#!/bin/bash
# Test: Caddy authentication on external ports.
#
# This file restarts caddy six times and makes ~15 http_check calls, each now
# allowed 20s (lib.sh: cost-14 bcrypt on every distinct wrong credential). An
# earlier version of this header declared `# TEST_TIMEOUT=900` to cover that.
# It was wrong twice over: 900 is exactly INSTANCE_TEST_DEFAULT_TIMEOUT in the
# promote workflow, so it changed nothing in CI, and it CUT the runner's own
# 3600s default by 4x for a customer running the suite by hand — the slow case
# it was supposed to protect. The ceiling is bounded in the code instead: the
# first caddy restart that does not come back fails the test immediately, so the
# worst case is one caddy budget plus the http_checks, not six budgets.
# Verifies bearer token, basic auth, query-param token, and 401 on unauthenticated requests.
source "$(dirname "$0")/../lib.sh"

is_serverless && test_skip "serverless (no caddy)"
[[ -f /etc/Caddyfile ]] || test_skip "no Caddyfile"

# FAILURES and fail_later/report_failures come from lib.sh
# http_check, find_caddy_ports, caddy_port_proto come from lib.sh

# ── OPEN_BUTTON_TOKEN is required ────────────────────────────────────

[[ -n "${OPEN_BUTTON_TOKEN:-}" ]] || test_fail "OPEN_BUTTON_TOKEN is not set"
echo "  OPEN_BUTTON_TOKEN=${OPEN_BUTTON_TOKEN:0:8}..."

# ── AUTH_EXCLUDE — ports that bypass auth ─────────────────────────────

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

# ── WEB_PASSWORD — set if absent, restart caddy ──────────────────────

auth_modified=false
if [[ -z "${WEB_PASSWORD:-}" ]]; then
    test_web_pass="testpass_$(openssl rand -hex 8)"
    echo "  WEB_PASSWORD not set — creating: ${test_web_pass}"

    sed -i '/^WEB_PASSWORD=/d' /etc/environment 2>/dev/null
    echo "WEB_PASSWORD=\"${test_web_pass}\"" >> /etc/environment
    export WEB_PASSWORD="$test_web_pass"

    echo "  restarting caddy to pick up WEB_PASSWORD..."
    supervisorctl restart caddy &>/dev/null \
        || test_fail "supervisorctl restart caddy failed — supervisord unreachable?"
    wait_for_caddy_ports || test_fail "caddy did not rebind its ports after restart (see WARN above)"
    auth_modified=true
else
    echo "  WEB_PASSWORD=${WEB_PASSWORD:0:8}..."
fi

basic_user="${WEB_USERNAME:-vastai}"
echo "  WEB_USERNAME=${WEB_USERNAME:-<not set>} (basic auth user: ${basic_user})"

# ── Find external ports ──────────────────────────────────────────────

# find_caddy_ports keys on `ss -tln`, so a caddy that has not finished binding
# yields NO ports and this file takes its `test_skip` exit below — a green run
# that asserted nothing about auth.
#
# This wait is UNCONDITIONAL, not inside the WEB_PASSWORD branch above. It used
# to sit there, so any template that SETS WEB_PASSWORD took the else-branch and
# reached find_caddy_ports with no readiness at all — the fail-open re-opened by
# a template variable. It is what makes the skip below mean "this image has no
# external caddy ports" rather than "we asked too early".
# `|| test_fail`, matching the six sibling calls in this file. Falling open here made
# the skip below mean "we asked too early" instead of "there are no external caddy
# ports", and neither this test nor its file is named in any INSTANCE_TEST_REQUIRE_PASS,
# so nothing converted the skip into a failure. The comment above already said this
# wait is what makes the skip trustworthy.
wait_for_caddy_ports || test_fail "caddy never bound its declared site ports — cannot tell whether the auth assertions below are absent or merely early"

find_caddy_ports
all_caddy_ports=("${REPLY[@]}")

test_port=""
for p in "${all_caddy_ports[@]}"; do
    if ! is_excluded "$p"; then
        test_port="$p"
        break
    fi
done

if [[ ${#all_caddy_ports[@]} -eq 0 ]]; then
    if $auth_modified; then
        sed -i '/^WEB_PASSWORD=/d' /etc/environment
        supervisorctl restart caddy &>/dev/null \
            || echo "  WARN: could not restart caddy while cleaning up"
        # Deliberately no wait here, and deliberately not the reverse. Reaching
        # this line means the wait at the top SUCCEEDED and the Caddyfile still
        # declares zero site ports, so wait_for_caddy_ports would iterate nothing
        # and return 0 on its first poll — a guard that cannot fail is worse than
        # none, because it reads as protection. 27-caddy-tls does its own wait.
    fi
    test_skip "no external caddy ports found"
fi

# Set TLS flag for curl if needed
tls_flag=""
if [[ -n "$test_port" ]]; then
    proto=$(caddy_port_proto "$test_port")
    [[ "$proto" == "https" ]] && tls_flag="-k"
    base_url="${proto}://127.0.0.1:${test_port}"
fi

# ── Auth tests (only if we have a non-excluded port) ─────────────────

if [[ -z "$test_port" ]]; then
    echo "  all caddy ports are in AUTH_EXCLUDE — skipping auth enforcement tests"
elif [[ -n "$test_port" ]]; then
    echo ""
    echo "  -- auth tests on port ${test_port} (${proto}) --"

    # No auth → 401
    http_check \
        "no credentials" 401 \
        $tls_flag "${base_url}/"

    # Bearer OPEN_BUTTON_TOKEN
    http_check \
        "bearer ${OPEN_BUTTON_TOKEN:0:8}... (OBT)" "200|302" \
        $tls_flag -H "Authorization: Bearer ${OPEN_BUTTON_TOKEN}" "${base_url}/"

    # Bearer WEB_PASSWORD
    http_check \
        "bearer ${WEB_PASSWORD:0:8}... (WEB_PASSWORD)" "200|302" \
        $tls_flag -H "Authorization: Bearer ${WEB_PASSWORD}" "${base_url}/"

    # Basic auth: correct user + WEB_PASSWORD
    http_check \
        "basic ${basic_user}:${WEB_PASSWORD:0:8}..." "200|302" \
        $tls_flag -u "${basic_user}:${WEB_PASSWORD}" "${base_url}/"

    # Basic auth: wrong username + correct password → 401
    http_check \
        "basic wronguser:${WEB_PASSWORD:0:8}..." 401 \
        $tls_flag -u "wronguser:${WEB_PASSWORD}" "${base_url}/"

    # If custom WEB_USERNAME, verify default vastai is rejected
    if [[ -n "${WEB_USERNAME:-}" && "${WEB_USERNAME}" != "vastai" ]]; then
        http_check \
            "basic vastai:${WEB_PASSWORD:0:8}... (should reject)" 401 \
            $tls_flag -u "vastai:${WEB_PASSWORD}" "${base_url}/"
    fi

    # Query param token → 302 redirect with cookie
    http_check \
        "?token=${OPEN_BUTTON_TOKEN:0:8}... (query param)" "302|200" \
        $tls_flag "${base_url}/?token=${OPEN_BUTTON_TOKEN}"

    # Wrong query param token → 401
    http_check \
        "?token=WRONG_TOKEN (query param)" 401 \
        $tls_flag "${base_url}/?token=wrong_token_value"

    # Cookie auth: capture cookie from ?token= redirect, then use it
    cookie_name="${VAST_CONTAINERLABEL:-C.unknown}_auth_token"
    cookie_val=$(curl -s --max-time "$HTTP_CHECK_MAX_TIME" ${tls_flag:-} -D- -o /dev/null \
        "${base_url}/?token=${OPEN_BUTTON_TOKEN}" 2>/dev/null \
        | grep -oP "${cookie_name}=\K[^;]+")
    if [[ -n "$cookie_val" ]]; then
        echo "  cookie received: ${cookie_name}=${cookie_val:0:8}..."

        # Valid cookie → 200
        http_check \
            "cookie ${cookie_name}=${cookie_val:0:8}..." "200|302" \
            $tls_flag -b "${cookie_name}=${cookie_val}" "${base_url}/"

        # Fake cookie value → 401
        http_check \
            "cookie ${cookie_name}=FAKE_VALUE" 401 \
            $tls_flag -b "${cookie_name}=fake_cookie_value" "${base_url}/"

        # Wrong cookie name, correct value → 401
        http_check \
            "cookie wrong_name=${cookie_val:0:8}..." 401 \
            $tls_flag -b "wrong_cookie_name=${cookie_val}" "${base_url}/"
    else
        fail_later "cookie-auth" "?token= did not return a Set-Cookie header"
    fi

    # Wrong bearer → 401
    http_check \
        "bearer WRONG_TOKEN" 401 \
        $tls_flag -H "Authorization: Bearer wrong_token_value" "${base_url}/"

    # Wrong basic auth → 401
    http_check \
        "basic nobody:wrongpassword" 401 \
        $tls_flag -u "nobody:wrongpassword" "${base_url}/"

    # ── Custom WEB_USERNAME test ─────────────────────────────────────
    # If WEB_USERNAME was not already set, temporarily set one to verify
    # that caddy uses it and rejects the default vastai.

    if [[ -z "${WEB_USERNAME:-}" ]]; then
        echo ""
        echo "  -- custom WEB_USERNAME test --"
        test_web_user="testadmin_$$"
        echo "  setting WEB_USERNAME=${test_web_user} and restarting caddy..."
        sed -i '/^WEB_USERNAME=/d' /etc/environment 2>/dev/null
        echo "WEB_USERNAME=\"${test_web_user}\"" >> /etc/environment
        supervisorctl restart caddy &>/dev/null \
        || test_fail "supervisorctl restart caddy failed — supervisord unreachable?"
        wait_for_caddy_ports || test_fail "caddy did not rebind its ports after restart (see WARN above)"

        # Custom user + WEB_PASSWORD → should work
        http_check \
            "basic ${test_web_user}:${WEB_PASSWORD:0:8}..." "200|302" \
            $tls_flag -u "${test_web_user}:${WEB_PASSWORD}" "${base_url}/"

        # Default vastai + WEB_PASSWORD → should be rejected
        http_check \
            "basic vastai:${WEB_PASSWORD:0:8}... (should reject)" 401 \
            $tls_flag -u "vastai:${WEB_PASSWORD}" "${base_url}/"

        # Restore: remove custom username and restart
        sed -i '/^WEB_USERNAME=/d' /etc/environment
        supervisorctl restart caddy &>/dev/null \
        || test_fail "supervisorctl restart caddy failed — supervisord unreachable?"
        wait_for_caddy_ports || test_fail "caddy did not rebind its ports after restart (see WARN above)"
        echo "  WEB_USERNAME removed, caddy restored"
    fi
fi

# ── Auth on additional ports ─────────────────────────────────────────

if [[ ${#all_caddy_ports[@]} -gt 1 ]]; then
    echo ""
    echo "  -- additional ports --"
    for port in "${all_caddy_ports[@]}"; do
        [[ "$port" == "$test_port" ]] && continue

        port_proto=$(caddy_port_proto "$port")
        tls_flag=""
        [[ "$port_proto" == "https" ]] && tls_flag="-k"

        s=$(curl -s --max-time "$HTTP_CHECK_MAX_TIME" ${tls_flag} -o /dev/null -w '%{http_code}' "${port_proto}://127.0.0.1:${port}/" 2>/dev/null)

        if is_excluded "$port"; then
            if [[ "$s" == "200" || "$s" == "302" ]]; then
                echo "  port ${port}: no auth → ${s} (AUTH_EXCLUDE, correct)"
            elif [[ "$s" == "502" ]]; then
                echo "  port ${port}: no auth → 502 (AUTH_EXCLUDE, backend down)"
            else
                fail_later "port-${port}-exclude" "AUTH_EXCLUDE but got ${s}, expected 200/302/502"
            fi
        else
            if [[ "$s" == "401" ]]; then
                echo "  port ${port}: no auth → 401 (correct)"
            elif [[ "$s" == "502" ]]; then
                echo "  port ${port}: no auth → 502 (backend down, auth not testable)"
            else
                fail_later "port-${port}-noauth" "expected 401, got ${s}"
            fi
        fi
    done
fi

# ── AUTH_EXCLUDE active test ─────────────────────────────────────────

if [[ -z "${AUTH_EXCLUDE:-}" && -n "$test_port" ]]; then
    echo ""
    echo "  -- AUTH_EXCLUDE test --"
    echo "  setting AUTH_EXCLUDE=${test_port} and restarting caddy..."
    sed -i '/^AUTH_EXCLUDE=/d' /etc/environment 2>/dev/null
    echo "AUTH_EXCLUDE=\"${test_port}\"" >> /etc/environment
    supervisorctl restart caddy &>/dev/null \
        || test_fail "supervisorctl restart caddy failed — supervisord unreachable?"
    wait_for_caddy_ports || test_fail "caddy did not rebind its ports after restart (see WARN above)"

    exc_proto=$(caddy_port_proto "$test_port")
    tls_flag=""
    [[ "$exc_proto" == "https" ]] && tls_flag="-k"

    s=$(curl -s --max-time "$HTTP_CHECK_MAX_TIME" ${tls_flag} -o /dev/null -w '%{http_code}' "${exc_proto}://127.0.0.1:${test_port}/" 2>/dev/null)
    if [[ "$s" == "200" || "$s" == "302" ]]; then
        echo "  port ${test_port}: no auth → ${s} (AUTH_EXCLUDE active, correct)"
    elif [[ "$s" == "502" ]]; then
        echo "  port ${test_port}: no auth → 502 (backend down, exclusion not verifiable)"
    else
        fail_later "auth-exclude-test" "AUTH_EXCLUDE=${test_port} but got ${s}, expected 200/302"
    fi

    sed -i '/^AUTH_EXCLUDE=/d' /etc/environment
    supervisorctl restart caddy &>/dev/null \
        || test_fail "supervisorctl restart caddy failed — supervisord unreachable?"
    wait_for_caddy_ports || test_fail "caddy did not rebind its ports after restart (see WARN above)"
    echo "  AUTH_EXCLUDE removed, caddy restored"
fi

# ── VAST_TCP_PORT / VAST_UDP_PORT listeners ──────────────────────────

echo ""
echo "  -- external port mappings --"
port_check_count=0

# check_port_mappings PREFIX PROTO SS_FLAG
#   PREFIX: VAST_TCP_PORT_ or VAST_UDP_PORT_
#   PROTO:  TCP or UDP (for display)
#   SS_FLAG: -tln or -uln
check_port_mappings() {
    local prefix="$1" proto="$2" ss_flag="$3"
    while IFS='=' read -r varname mapped_port; do
        [[ -n "$varname" ]] || continue
        local container_port="${varname#"${prefix}"}"
        [[ "$container_port" == "22" ]] && continue

        local check_port
        if (( container_port >= 70000 )); then
            check_port="$mapped_port"
        else
            check_port="$container_port"
        fi

        if ss "$ss_flag" | grep -qE ":(${check_port}) "; then
            echo "  ${proto} ${container_port} (host ${mapped_port}): listening on ${check_port}"
        else
            echo "  WARN: ${proto} ${container_port} (host ${mapped_port}): not listening on ${check_port}"
        fi
        port_check_count=$((port_check_count + 1))
    done < <(env | grep "^${prefix}" | sort)
}

check_port_mappings "VAST_TCP_PORT_" "TCP" "-tln"
check_port_mappings "VAST_UDP_PORT_" "UDP" "-uln"

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

# ── Cleanup ──────────────────────────────────────────────────────────

if $auth_modified; then
    echo ""
    echo "  cleaning up test password and restarting caddy..."
    sed -i '/^WEB_PASSWORD=/d' /etc/environment
    supervisorctl restart caddy &>/dev/null \
        || test_fail "supervisorctl restart caddy failed — supervisord unreachable?"
    wait_for_caddy_ports || test_fail "caddy did not rebind its ports after restart (see WARN above)"
fi

# ── Report ───────────────────────────────────────────────────────────

report_failures

excluded_count=${#EXCLUDED_PORTS[@]}
if [[ $excluded_count -gt 0 ]]; then
    test_pass "caddy auth verified (test port: ${test_port:-none}, ${excluded_count} excluded)"
else
    test_pass "caddy auth verified on port ${test_port}"
fi
