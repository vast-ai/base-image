#!/bin/bash
# Test: supervisor service states and conditional services.
# Runs after provisioning (12) since provisioning can register new services.
source "$(dirname "$0")/../lib.sh"

# ── Core services (always expected on base image) ─────────────────────

# Verify each installed .conf appears in supervisorctl status
sup_status=$(supervisorctl status 2>/dev/null)
for conf in /etc/supervisor/conf.d/*.conf; do
    [[ -f "$conf" ]] || continue
    name=$(basename "$conf" .conf)
    if ! echo "$sup_status" | grep -q "^${name} "; then
        echo "  WARN: ${name}.conf exists but not in supervisorctl status"
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
    if [[ -f /etc/supervisor/conf.d/cron.conf ]]; then
        assert_service_running "cron"
    fi
    test_pass "serverless: services correctly stopped, cron running"
fi

# Non-serverless: assert core services running
for name in instance_portal caddy cron; do
    if [[ -f "/etc/supervisor/conf.d/${name}.conf" ]]; then
        assert_service_running "$name"
        echo "  ${name}: RUNNING"
    fi
done

# ── Jupyter (special case: .launch vs supervisor) ─────────────────────

check_jupyter() {
    # .launch manages jupyter in SSH/Jupyter run modes (most common).
    # Supervisor manages it in docker entrypoint mode or with JUPYTER_OVERRIDE=true.
    local launch_manages=false
    if [[ -f /.launch ]] && grep -qi jupyter /.launch && [[ "${JUPYTER_OVERRIDE,,}" != "true" ]]; then
        launch_manages=true
    fi

    if $launch_manages; then
        # .launch runs jupyter on 0.0.0.0:8080 with TLS
        if pgrep -f "jupyter" &>/dev/null; then
            echo "  jupyter: .launch-managed process running"
        else
            test_fail "jupyter: .launch should be managing jupyter but no jupyter process found"
        fi
        if ss -tln | grep -q ":8080 "; then
            echo "  jupyter: listening on port 8080"
        else
            test_fail "jupyter: .launch-managed jupyter not listening on port 8080"
        fi
        # Verify it's bound to all interfaces (0.0.0.0), not just localhost
        if ss -tln | grep ":8080 " | grep -q "0.0.0.0:"; then
            echo "  jupyter: bound to all interfaces"
        else
            echo "  WARN: jupyter on port 8080 but not bound to 0.0.0.0"
        fi
        # Supervisor jupyter should have exited since .launch is managing
        if [[ -f /etc/supervisor/conf.d/jupyter.conf ]]; then
            local sup_status
            sup_status=$(supervisorctl status jupyter 2>/dev/null | awk '{print $2}')
            case "$sup_status" in
                EXITED)
                    echo "  jupyter: supervisor correctly deferred to .launch"
                    ;;
                *)
                    echo "  WARN: supervisor jupyter in state ${sup_status:-unknown} (expected EXITED)"
                    ;;
            esac
        fi
    elif [[ -f /etc/supervisor/conf.d/jupyter.conf ]]; then
        # Supervisor-managed jupyter (docker entrypoint mode)
        if portal_has_entry "jupyter" && ! is_serverless; then
            assert_service_running "jupyter"
            if wait_for_port 18080 10; then
                echo "  jupyter: supervisor-managed, port 18080 listening"
            else
                echo "  WARN: jupyter running but port 18080 not listening yet"
            fi
        else
            local sup_status
            sup_status=$(supervisorctl status jupyter 2>/dev/null | awk '{print $2}')
            case "$sup_status" in
                STOPPED|EXITED|FATAL)
                    echo "  jupyter: correctly stopped"
                    ;;
                *)
                    test_fail "jupyter should be stopped but is: ${sup_status:-unknown}"
                    ;;
            esac
        fi
    else
        echo "  skip: jupyter (no .launch jupyter and no supervisor conf)"
    fi
}

check_jupyter

# ── Other conditional services ────────────────────────────────────────

# Service name, portal search term, internal port
declare -a SERVICES=(
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
        assert_service_running "$name"
        if wait_for_port "$port" 10; then
            echo "  ${name}: running, port ${port} listening"
        else
            echo "  WARN: ${name} running but port ${port} not listening yet"
        fi
    else
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

test_pass "all service states verified"
