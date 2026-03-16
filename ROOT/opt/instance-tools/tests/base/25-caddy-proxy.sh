#!/bin/bash
# Test: caddy reverse proxy is running and configured.
source "$(dirname "$0")/../lib.sh"

is_serverless && test_skip "caddy not expected in serverless mode"

# Caddy PID alive
caddy_pid=$(pidof caddy 2>/dev/null) || test_fail "caddy not running"

# Has listening sockets
caddy_listeners=$(ss -tlnp 2>/dev/null | grep "pid=${caddy_pid}" | wc -l)
[[ "$caddy_listeners" -gt 0 ]] || test_fail "caddy has no listening sockets"

# Caddyfile exists and is non-empty
assert_file_exists /etc/Caddyfile
[[ -s /etc/Caddyfile ]] || test_fail "/etc/Caddyfile is empty"

# Caddyfile contains reverse_proxy directive
grep -q "reverse_proxy" /etc/Caddyfile || test_fail "/etc/Caddyfile missing reverse_proxy directive"

test_pass "caddy running with ${caddy_listeners} listener(s), Caddyfile configured"
