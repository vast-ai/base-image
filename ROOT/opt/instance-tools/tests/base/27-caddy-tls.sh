#!/bin/bash
# Test: Caddy TLS termination — verifies HTTPS works when ENABLE_HTTPS=true
# and plain HTTP works when ENABLE_HTTPS is not set.
# Instance cert and key must always be present at /etc/instance.{crt,key}.
source "$(dirname "$0")/../lib.sh"

is_serverless && test_skip "serverless (no caddy)"
[[ -f /etc/Caddyfile ]] || test_skip "no Caddyfile"

CERT_PATH="/etc/instance.crt"
KEY_PATH="/etc/instance.key"

# FAILURES and fail_later/report_failures come from lib.sh

# ── Certs must always be present ──────────────────────────────────────

[[ -f "$CERT_PATH" ]] || test_fail "instance certificate not found: ${CERT_PATH}"
[[ -f "$KEY_PATH" ]] || test_fail "instance key not found: ${KEY_PATH}"
echo "  cert: ${CERT_PATH} (present)"
echo "  key: ${KEY_PATH} (present)"

# Validate cert and key are well-formed
openssl x509 -in "$CERT_PATH" -noout 2>/dev/null \
    || test_fail "invalid certificate: ${CERT_PATH}"
openssl rsa -in "$KEY_PATH" -check -noout 2>/dev/null \
    || test_fail "invalid key: ${KEY_PATH}"
echo "  cert and key are valid"

# wait_for_caddy comes from lib.sh

# ── Find a test port ──────────────────────────────────────────────────

find_caddy_ports
test_port="${REPLY[0]:-}"

[[ -n "$test_port" ]] || test_skip "no external caddy port found"
echo "  test port: ${test_port}"

# ── Helpers ───────────────────────────────────────────────────────────

caddyfile_has_tls() {
    [[ "$(caddy_port_proto "$test_port")" == "https" ]]
}

# Inspect the certificate served by caddy on a given port
inspect_served_cert() {
    local port="$1"
    local cert_info
    cert_info=$(echo | openssl s_client -connect "127.0.0.1:${port}" \
        -servername localhost 2>/dev/null)
    if [[ -z "$cert_info" ]]; then
        fail_later "cert-inspect-${port}" "could not retrieve certificate from port ${port}"
        return
    fi

    local issuer subject expiry
    issuer=$(echo "$cert_info" | openssl x509 -noout -issuer 2>/dev/null | sed 's/issuer=//')
    subject=$(echo "$cert_info" | openssl x509 -noout -subject 2>/dev/null | sed 's/subject=//')
    expiry=$(echo "$cert_info" | openssl x509 -noout -enddate 2>/dev/null | sed 's/notAfter=//')
    echo "  cert subject: ${subject}"
    echo "  cert issuer: ${issuer}"
    echo "  cert expiry: ${expiry}"

    # Check trust — instance certs are typically self-signed or from a private CA
    local verify_code
    verify_code=$(echo | openssl s_client -connect "127.0.0.1:${port}" \
        -servername localhost 2>&1 | grep -oP 'verify return code: \K\d+' || echo "?")
    if [[ "$verify_code" == "0" ]]; then
        echo "  cert validation: trusted (signed by known CA)"
    else
        echo "  cert validation: NOT trusted (verify code: ${verify_code} — expected for instance certs)"
    fi
}

# Check HTTP → HTTPS redirect on a TLS-enabled port
check_http_redirect() {
    local port="$1"
    local label="${2:-}"
    local redirect_status
    redirect_status=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' \
        "http://127.0.0.1:${port}/" 2>/dev/null)
    if [[ "$redirect_status" =~ ^(301|302|307|308)$ ]]; then
        # Verify the redirect Location points to https
        local redirect_loc
        redirect_loc=$(curl -s --max-time 5 -D- -o /dev/null \
            "http://127.0.0.1:${port}/" 2>/dev/null \
            | grep -iP '^location:' | head -1)
        echo "  http → https redirect${label}: ${redirect_status} (${redirect_loc})"
        if ! echo "$redirect_loc" | grep -qi "https://"; then
            fail_later "tls-redirect-target${label}" "redirect Location does not point to https"
        fi
    elif [[ "$redirect_status" == "000" ]]; then
        # Some Caddy versions reject plain HTTP at the connection level
        echo "  http → https${label}: connection rejected (TLS-only listener, acceptable)"
    else
        fail_later "tls-http-redirect${label}" "http://127.0.0.1:${port} returned ${redirect_status}, expected 301/302/307/308 redirect to HTTPS"
    fi
}

current_https="${ENABLE_HTTPS:-false}"
echo "  ENABLE_HTTPS=${current_https}"

# ── Test current state ────────────────────────────────────────────────

if caddyfile_has_tls; then
    echo ""
    echo "  -- TLS currently active --"

    # HTTPS should work (with -k for self-signed)
    http_check "tls-active-https" "200|302|401" -k "https://127.0.0.1:${test_port}/"

    inspect_served_cert "$test_port"
    check_http_redirect "$test_port"
else
    echo ""
    echo "  -- plain HTTP (no TLS) --"

    # HTTP should work
    http_check "http-active" "200|302|401" "http://127.0.0.1:${test_port}/"

    # HTTPS should NOT work (no TLS configured).
    # Note: there is no HTTPS→HTTP reverse redirect — caddy only supports
    # http_redirect (HTTP→HTTPS) via listener wrappers. When TLS is off,
    # HTTPS connections simply fail at the socket level.
    https_status=$(curl -sk --max-time 3 -o /dev/null -w '%{http_code}' \
        "https://127.0.0.1:${test_port}/" 2>/dev/null)
    if [[ "$https_status" == "000" ]]; then
        echo "  https://127.0.0.1:${test_port} → connection failed (correct, no TLS listener)"
    else
        fail_later "no-tls-https" "https://127.0.0.1:${test_port} returned ${https_status} but TLS is not configured"
    fi
fi

# ── Toggle test: verify the opposite mode works ──────────────────────
# Certs are always present, so we can freely toggle ENABLE_HTTPS.
# If currently HTTP → enable HTTPS, verify, restore.
# If currently HTTPS → disable, verify HTTP, restore.

echo ""

if ! caddyfile_has_tls; then
    echo "  -- enabling HTTPS --"
    sed -i '/^ENABLE_HTTPS=/d' /etc/environment 2>/dev/null
    echo 'ENABLE_HTTPS="true"' >> /etc/environment
    export ENABLE_HTTPS=true
    supervisorctl restart caddy &>/dev/null
    wait_for_caddy "$test_port" "https"

    if caddyfile_has_tls; then
        # Verify HTTPS works
        http_check "tls-enable" "200|302|401" -k "https://127.0.0.1:${test_port}/"

        inspect_served_cert "$test_port"
        check_http_redirect "$test_port" " (after enable)"
    else
        fail_later "tls-caddyfile" "ENABLE_HTTPS=true but Caddyfile has no tls directive"
    fi

    # Restore HTTP mode
    echo "  restoring HTTP mode..."
    sed -i '/^ENABLE_HTTPS=/d' /etc/environment
    export ENABLE_HTTPS=false
    supervisorctl restart caddy &>/dev/null
    wait_for_caddy "$test_port" "http"

    http_check "http-restore" "200|302|401" "http://127.0.0.1:${test_port}/"
else
    echo "  -- disabling HTTPS (verify plain HTTP) --"
    sed -i '/^ENABLE_HTTPS=/d' /etc/environment 2>/dev/null
    echo 'ENABLE_HTTPS="false"' >> /etc/environment
    export ENABLE_HTTPS=false
    supervisorctl restart caddy &>/dev/null
    wait_for_caddy "$test_port" "http"

    if ! caddyfile_has_tls; then
        http_check "http-disable" "200|302|401" "http://127.0.0.1:${test_port}/"
    else
        fail_later "tls-disable" "ENABLE_HTTPS=false but Caddyfile still has tls directive"
    fi

    # Restore HTTPS mode
    echo "  restoring HTTPS mode..."
    sed -i '/^ENABLE_HTTPS=/d' /etc/environment
    echo 'ENABLE_HTTPS="true"' >> /etc/environment
    export ENABLE_HTTPS=true
    supervisorctl restart caddy &>/dev/null
    wait_for_caddy "$test_port" "https"

    http_check "tls-restore" "200|302|401" -k "https://127.0.0.1:${test_port}/"
fi

# ── Report ────────────────────────────────────────────────────────────

report_failures

test_pass "caddy TLS verified (port: ${test_port}, ENABLE_HTTPS=${current_https})"
