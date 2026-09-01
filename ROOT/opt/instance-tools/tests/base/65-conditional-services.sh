#!/bin/bash
# Test: supervisor service states and conditional services.
# Runs after provisioning (12) since provisioning can register new services.
source "$(dirname "$0")/../lib.sh"

# FAILURES and fail_later/report_failures come from lib.sh

# ── Verify .conf files are registered ─────────────────────────────────

# This file's first touch of supervisord is a raw supervisorctl in a subshell.
# It runs after provisioning so it is not the race base/10-supervisor hit, but an
# unreachable socket here yields an EMPTY status and every service below would be
# reported "not registered" — a wrong answer rather than no answer.
wait_for_supervisor 60 || test_fail "supervisord RPC socket did not come up after 60s"
sup_status=$(supervisorctl status 2>/dev/null)
for conf in /etc/supervisor/conf.d/*.conf; do
    [[ -f "$conf" ]] || continue
    name=$(basename "$conf" .conf)
    if ! echo "$sup_status" | grep -q "^${name} "; then
        echo "  WARN: ${name}.conf exists but not in supervisorctl status"
    fi
done

# ── Helper: check service state ──────────────────────────────────────

check_running() {
    local name="$1"
    if service_running "$name"; then
        echo "  ${name}: RUNNING"
    else
        local status
        status=$(supervisorctl status "$name" 2>/dev/null | awk '{print $2}')
        fail_later "$name" "expected RUNNING, got ${status:-unknown}"
    fi
}

check_stopped() {
    local name="$1"
    local status
    status=$(supervisorctl status "$name" 2>/dev/null | awk '{print $2}')
    case "$status" in
        STOPPED|EXITED|FATAL)
            echo "  ${name}: correctly stopped (${status})"
            ;;
        *)
            fail_later "$name" "expected stopped, got ${status:-unknown}"
            ;;
    esac
}

# ── Core services ─────────────────────────────────────────────────────

# Track which services we've already checked to avoid duplicates
declare -A CHECKED=()

if is_serverless; then
    # Wait for services to finish exiting (startsecs=5 + sleep 6 in exit_serverless.sh)
    sleep 8
    # Serverless: caddy/portal/jupyter/tensorboard/syncthing/tunnel_manager should be stopped
    for name in caddy instance_portal jupyter tensorboard syncthing tunnel_manager; do
        if [[ -f "/etc/supervisor/conf.d/${name}.conf" ]]; then
            check_stopped "$name"
            CHECKED[$name]=1
        fi
    done
    # cron still runs in serverless
    if [[ -f /etc/supervisor/conf.d/cron.conf ]]; then
        check_running "cron"
        CHECKED[cron]=1
    fi
else
    # Non-serverless: assert core services running
    for name in instance_portal caddy cron; do
        if [[ -f "/etc/supervisor/conf.d/${name}.conf" ]]; then
            check_running "$name"
            CHECKED[$name]=1
        fi
    done
fi

# ── Jupyter (special case: .launch vs supervisor) ─────────────────────

check_jupyter() {
    local launch_manages=false
    if [[ -f /.launch ]] && grep -qi jupyter /.launch && [[ "${JUPYTER_OVERRIDE,,}" != "true" ]]; then
        launch_manages=true
    fi

    if is_serverless; then
        # Our supervisor jupyter service is correctly stopped (checked above).
        # But .launch may still be running jupyter if the template was configured
        # with jupyter launch mode — that's a platform-level concern, not ours.
        if [[ -n "${JUPYTER_TOKEN:-}" ]] || [[ -n "${JUPYTER_DIR:-}" ]] || [[ -n "${JUPYTER_TYPE:-}" ]]; then
            echo "  WARN: serverless instance has JUPYTER_* env vars set — template may be configured for jupyter launch mode"
            if pgrep -f "jupyter" &>/dev/null; then
                echo "  WARN: .launch-managed jupyter is running (platform-managed, not our service)"
            fi
        fi
        # Supervisor jupyter already checked via check_stopped in core section
        return
    fi

    if $launch_manages; then
        # .launch runs jupyter on 0.0.0.0:8080 with TLS
        if pgrep -f "jupyter" &>/dev/null; then
            echo "  jupyter: .launch-managed process running"
        else
            fail_later "jupyter" ".launch should be managing it but no process found"
        fi
        if ss -tln | grep -q ":8080 "; then
            echo "  jupyter: listening on port 8080"
            # .launch runs jupyter PUBLICLY on purpose, so 0.0.0.0 is the expected
            # answer here — the opposite direction from every other bind check. This read
            # the whole `ss` line, which matches the peer column (always 0.0.0.0:* for a
            # listener), so it reported "bound to all interfaces" for any listener at all
            # and the WARN below could never fire: a check that cannot fail. Same defect
            # measured live on an unsloth-studio QA cell, where the inverted direction
            # made it fail every time instead (L082).
            if listener_is_public 8080; then
                echo "  jupyter: bound to all interfaces"
            else
                echo "  WARN: jupyter on port 8080 but not bound to 0.0.0.0 ($(listener_local_addr 8080))"
            fi
        else
            fail_later "jupyter" ".launch-managed but not listening on port 8080"
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
        if portal_has_entry "jupyter"; then
            check_running "jupyter"
            # A port miss is a FAILURE, not a remark. `check_running` already treats a
            # wrong supervisor state as one, and "RUNNING but never bound" is the case
            # neither autorestart nor the state word can see — the whole reason
            # 67-service-functionality grew assert_service_serving. 10s was also below
            # every measured readiness floor in this repo (L070 starts at 60).
            if wait_for_port 18080 "${SERVICE_SERVING_TIMEOUT:-60}"; then
                echo "  jupyter: supervisor-managed, port 18080 listening"
            else
                fail_later "jupyter-port" "jupyter is RUNNING but nothing is listening on 18080 after ${SERVICE_SERVING_TIMEOUT:-60}s"
            fi
        else
            check_stopped "jupyter"
        fi
    else
        echo "  skip: jupyter (no .launch jupyter and no supervisor conf)"
    fi
}

check_jupyter
CHECKED[jupyter]=1

# ── Other conditional services ────────────────────────────────────────

declare -a SERVICES=(
    "tensorboard:tensorboard:16006"
    "syncthing:syncthing:18384"
    "tunnel_manager:instance portal:11112"
)

for entry in "${SERVICES[@]}"; do
    IFS=: read -r name search_term port <<< "$entry"

    if [[ -n "${CHECKED[$name]:-}" ]]; then
        continue
    fi

    if [[ ! -f "/etc/supervisor/conf.d/${name}.conf" ]]; then
        echo "  skip: ${name} (.conf not installed)"
        continue
    fi

    if portal_has_entry "$search_term" && ! is_serverless; then
        check_running "$name"
        # Same rule as jupyter above: RUNNING and not serving is a failure.
        if wait_for_port "$port" "${SERVICE_SERVING_TIMEOUT:-60}"; then
            echo "  ${name}: port ${port} listening"
        else
            fail_later "${name}-port" "${name} is RUNNING but nothing is listening on ${port} after ${SERVICE_SERVING_TIMEOUT:-60}s"
        fi
    else
        check_stopped "$name"
    fi
done

# ── Report ────────────────────────────────────────────────────────────

report_failures

test_pass "all service states verified"
