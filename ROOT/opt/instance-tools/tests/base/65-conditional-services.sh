#!/bin/bash
# Test: conditional services match portal.yaml configuration.
source "$(dirname "$0")/../lib.sh"

# Service name, portal search term, internal port
declare -a SERVICES=(
    "jupyter:jupyter:18080"
    "tensorboard:tensorboard:16006"
    "syncthing:syncthing:18384"
    "tunnel_manager:instance portal:11112"
)

for entry in "${SERVICES[@]}"; do
    IFS=: read -r name search_term port <<< "$entry"

    # Only check services whose .conf exists
    if [[ ! -f "/etc/supervisor/conf.d/${name}.conf" ]]; then
        echo "  skip: ${name} (.conf not installed)"
        continue
    fi

    if portal_has_entry "$search_term" && ! is_serverless; then
        # Should be running — check status but allow EXITED (some services exit cleanly
        # when dependencies aren't ready, e.g. jupyter exits if /.provisioning exists)
        status=$(supervisorctl status "$name" 2>/dev/null | awk '{print $2}')
        case "$status" in
            RUNNING)
                if wait_for_port "$port" 10; then
                    echo "  ${name}: running, port ${port} listening"
                else
                    echo "  WARN: ${name} running but port ${port} not listening yet"
                fi
                ;;
            EXITED)
                echo "  WARN: ${name} configured but EXITED (may need restart)"
                ;;
            *)
                test_fail "service ${name} in unexpected state: ${status:-unknown}"
                ;;
        esac
    else
        # Should be stopped (not in portal.yaml, or serverless)
        status=$(supervisorctl status "$name" 2>/dev/null | awk '{print $2}')
        case "$status" in
            STOPPED|EXITED|FATAL)
                echo "  ${name}: correctly stopped"
                ;;
            *)
                test_fail "service ${name} should be stopped but is: ${status:-unknown}"
                ;;
        esac
    fi
done

test_pass "conditional services match configuration"
