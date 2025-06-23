#!/bin/bash

kill_subprocesses() {
    local pid=$1
    local subprocesses=$(pgrep -P "$pid")
    
    for process in $subprocesses; do
        kill_subprocesses "$process"
    done
    
    if [[ -n "$subprocesses" ]]; then
        kill -TERM $subprocesses 2>/dev/null
    fi
}

cleanup() {
    kill_subprocesses $$
    sleep 2
    pkill -KILL -P $$ 2>/dev/null
    exit 0
}

trap cleanup EXIT INT TERM

set -a
. /etc/environment 2>/dev/null
. ${WORKSPACE}/.env 2>/dev/null
set +a

# Run the caddy configurator
cd /opt/portal-aio/caddy_manager
/opt/portal-aio/venv/bin/python caddy_config_manager.py | tee -a "/var/log/portal/${PROC_NAME}.log"

# Ensure the portal config file exists if running without PORTAL_CONFIG
touch /etc/portal.yaml

if [[ -f /etc/Caddyfile ]]; then
    # Frontend log viewer will force a page reload if this string is detected
    echo "Starting Caddy..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    /opt/portal-aio/caddy_manager/caddy run --config /etc/Caddyfile 2>&1 | tee -a "/var/log/portal/${PROC_NAME}.log"
else
    echo "Not Starting Caddy - No config file was generated" | tee -a "/var/log/portal/${PROC_NAME}.log"
fi
