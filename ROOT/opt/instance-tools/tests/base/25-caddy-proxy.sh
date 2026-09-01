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
# `pidof caddy` matches by BASENAME, and caddy_config_manager.py spawns transient
# `caddy hash-password` (bcrypt cost 14 — measured at 43s on a contended host) and
# `caddy fmt` children. With two alive, pidof returns "P1 P2", a space-separated string
# that can never appear in an `ss` row, so a perfectly healthy image failed with
# "caddy has no listening sockets". 20-portal.sh does not protect against this: the
# config manager writes /etc/portal.yaml before it hashes anything, so the portal
# releases while bcrypt is still running — on first boot, not only stop/start.
#
# Match the SERVER process by its full command line, iterate every candidate rather than
# assuming one, and retry on a deadline instead of judging a single instant.
_caddy_deadline=$(( SECONDS + CADDY_READY_TIMEOUT ))
caddy_listeners=0
while :; do
    caddy_pids=$(pgrep -f '(^|/)caddy run' 2>/dev/null || true)
    if [[ -n "$caddy_pids" ]]; then
        # Anchor the pid with a trailing comma: `pid=1234` is a prefix of `pid=12345`.
        _rows=$(ss -tlnp 2>/dev/null || true)
        caddy_listeners=0
        while IFS= read -r _p; do
            [[ -n "$_p" ]] || continue
            caddy_listeners=$(( caddy_listeners + $(grep -c "pid=${_p}," <<< "$_rows") ))
        done <<< "$caddy_pids"
        (( caddy_listeners > 0 )) && break
    fi
    (( SECONDS >= _caddy_deadline )) && test_fail \
        "caddy is RUNNING under supervisord but has no listening sockets after ${CADDY_READY_TIMEOUT}s (server pids: ${caddy_pids:-none})"
    sleep 1
done
caddy_pid=$(head -1 <<< "$caddy_pids")

# Caddyfile exists and is non-empty
assert_file_exists /etc/Caddyfile
[[ -s /etc/Caddyfile ]] || test_fail "/etc/Caddyfile is empty"

# Caddyfile contains reverse_proxy directive
grep -q "reverse_proxy" /etc/Caddyfile || test_fail "/etc/Caddyfile missing reverse_proxy directive"

test_pass "caddy running with ${caddy_listeners} listener(s), Caddyfile configured"
