#!/bin/bash
# Test: caddy reverse proxy is running and configured.
source "$(dirname "$0")/../lib.sh"

is_serverless && test_skip "caddy not expected in serverless mode"

# Readiness from the socket, identity from pidof.
#
# `pidof caddy` alone was the readiness gate here, and it is the same defect as
# base/10-supervisor: it answers "was it forked", while caddy.conf sets
# `startsecs=5`, so caddy is legitimately STARTING for the first five seconds of
# the instance's life. assert_service_running waits for RUNNING.
#
# But RUNNING answers for the WRAPPER. caddy.sh runs caddy_config_manager.py —
# cost-14 bcrypt per proxied app, 43s measured on a contended host — before it
# execs `caddy run`, so the BINARY appears well after supervisord calls the
# program started. A single-shot pidof here would race that chain rather than
# supervisord, and fail with a new message on a healthy image. Same class of
# defect, one layer further in, so it gets the same bounded wait.
#
# pidof stays for IDENTITY, which supervisord genuinely cannot supply: caddy.sh
# is a wrapper script, so `supervisorctl pid caddy` returns the shell's pid and
# the listener attribution below would match nothing.
assert_service_running caddy
caddy_pid=""
_caddy_deadline=$(( SECONDS + CADDY_READY_TIMEOUT ))
while :; do
    caddy_pid=$(pidof caddy 2>/dev/null) && break
    (( SECONDS >= _caddy_deadline )) && test_fail \
        "caddy is RUNNING under supervisord but its binary never started (${CADDY_READY_TIMEOUT}s)"
    sleep 1
done

# Has listening sockets
caddy_listeners=$(ss -tlnp 2>/dev/null | grep "pid=${caddy_pid}" | wc -l)
[[ "$caddy_listeners" -gt 0 ]] || test_fail "caddy has no listening sockets"

# Caddyfile exists and is non-empty
assert_file_exists /etc/Caddyfile
[[ -s /etc/Caddyfile ]] || test_fail "/etc/Caddyfile is empty"

# Caddyfile contains reverse_proxy directive
grep -q "reverse_proxy" /etc/Caddyfile || test_fail "/etc/Caddyfile missing reverse_proxy directive"

test_pass "caddy running with ${caddy_listeners} listener(s), Caddyfile configured"
