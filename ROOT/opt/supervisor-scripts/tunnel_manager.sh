#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh" "/var/log/${PROC_NAME}.log"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"
. "${utils}/exit_portal.sh" "instance portal"

# More stable default
export TUNNEL_TRANSPORT_PROTOCOL=${TUNNEL_TRANSPORT_PROTOCOL:-http2}

cd /opt/portal-aio/tunnel_manager
# Log outside of /var/log/portal
/opt/portal-aio/venv/bin/fastapi run --host 127.0.0.1 --port 11112 tunnel_manager.py 2>&1
