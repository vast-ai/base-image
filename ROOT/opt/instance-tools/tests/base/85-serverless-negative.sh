#!/bin/bash
# Test: serverless mode — verify non-serverless services are stopped.
source "$(dirname "$0")/../lib.sh"

is_serverless || test_skip "not in serverless mode"

# Services that should be stopped in serverless mode
for name in caddy instance_portal jupyter tensorboard tunnel_manager syncthing; do
    if [[ -f "/etc/supervisor/conf.d/${name}.conf" ]]; then
        assert_service_stopped "$name"
        echo "  ${name}: correctly stopped"
    fi
done

# caddy should not be running
! pidof caddy &>/dev/null || test_fail "caddy process still running in serverless mode"

# Key ports should NOT be listening
for port in 11111 11112 18080; do
    if ss -tln | grep -q ":${port} "; then
        test_fail "port ${port} is listening in serverless mode"
    fi
done

# supervisord itself must still be running
pgrep -f supervisord &>/dev/null || test_fail "supervisord not running"

test_pass "serverless negative checks passed"
