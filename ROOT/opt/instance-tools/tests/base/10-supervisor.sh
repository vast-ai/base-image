#!/bin/bash
# Test: supervisor is running and expected services are in correct state.
source "$(dirname "$0")/../lib.sh"

# Supervisor itself must be running
pgrep -f supervisord &>/dev/null || test_fail "supervisord not running"

# Build list of expected programs from installed .conf files
declare -A EXPECTED_SERVICES=(
    [caddy]=caddy
    [instance_portal]=instance_portal
    [jupyter]=jupyter
    [tensorboard]=tensorboard
    [tunnel_manager]=tunnel_manager
    [syncthing]=syncthing
    [cron]=cron
)

present_services=()
for name in "${!EXPECTED_SERVICES[@]}"; do
    if [[ -f "/etc/supervisor/conf.d/${name}.conf" ]]; then
        present_services+=("$name")
    fi
done

# Verify each present service appears in supervisorctl status
sup_status=$(supervisorctl status 2>/dev/null)
for name in "${present_services[@]}"; do
    if ! echo "$sup_status" | grep -q "^${name} "; then
        echo "WARN: ${name}.conf exists but not in supervisorctl status"
    fi
done

if is_serverless; then
    # Serverless: caddy/portal/jupyter/tensorboard/syncthing/tunnel_manager should be stopped
    for name in caddy instance_portal jupyter tensorboard syncthing tunnel_manager; do
        if [[ -f "/etc/supervisor/conf.d/${name}.conf" ]]; then
            assert_service_stopped "$name"
        fi
    done
    # cron still runs in serverless
    if [[ -f "/etc/supervisor/conf.d/cron.conf" ]]; then
        assert_service_running "cron"
    fi
    test_pass "supervisord running, serverless services correctly stopped"
fi

# Non-serverless: assert key services running
for name in instance_portal caddy cron; do
    if [[ -f "/etc/supervisor/conf.d/${name}.conf" ]]; then
        assert_service_running "$name"
    fi
done

test_pass "supervisord running, expected services up"
