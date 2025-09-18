#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"

# Run the caddy configurator
cd /opt/portal-aio/caddy_manager
/opt/portal-aio/venv/bin/python caddy_config_manager.py

# Ensure the portal config file exists if running without PORTAL_CONFIG
touch /etc/portal.yaml

if [[ -f /etc/Caddyfile ]]; then
    # Frontend log viewer will force a page reload if this string is detected
    echo "Starting Caddy..." 
    /opt/portal-aio/caddy_manager/caddy run --config /etc/Caddyfile 2>&1
    exit $?
else
    echo "Skipping Caddy startup - No config file was generated"
fi
