#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"

# Run the caddy configurator
cd /opt/portal-aio/caddy_manager
/opt/portal-aio/venv/bin/python caddy_config_manager.py

# Publish an EMPTY config only when there is genuinely nothing to publish, and
# make it parseable rather than zero-byte.
#
# This used to be an unconditional `touch`. It ran even when
# caddy_config_manager.py had raised — its main() catches Exception and prints —
# so one malformed PORTAL_CONFIG entry produced a zero-byte /etc/portal.yaml on
# /etc, which survives stop/start. Six services then read it, found their name
# absent, and self-skipped for the life of the instance: no Instance Portal at
# all, every one of them reporting "not in /etc/portal.yaml" when the truth was
# that the thing which writes their configuration had crashed. Verified on a
# real boot.
#
# `applications: {}` says "no apps configured" in the same shape a real config
# has, so a reader can tell it apart from a half-written file.
if [[ ! -s /etc/portal.yaml ]]; then
    printf 'applications: {}\n' > /etc/portal.yaml
fi

if [[ -f /etc/Caddyfile ]]; then
    # Frontend log viewer will force a page reload if this string is detected
    echo "Starting Caddy..." 
    /opt/portal-aio/caddy_manager/caddy run --config /etc/Caddyfile 2>&1
    exit $?
else
    echo "Skipping Caddy startup - No config file was generated"
fi
