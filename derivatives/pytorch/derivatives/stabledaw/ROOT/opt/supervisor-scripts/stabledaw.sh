#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

# Serverless: skip the portal-config gate when running serverless
if [[ "${SERVERLESS:-false}" != "true" ]]; then
    . "${utils}/exit_portal.sh" "StableDAW"
fi

[[ -f /venv/main/bin/activate ]] && . /venv/main/bin/activate

# Wait for provisioning to complete before starting
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until provisioning completes (/.provisioning present)"
    sleep 5
done

STABLEDAW_DIR=${STABLEDAW_DIR:-/opt/stabledaw}

# Launch the backend + built SPA on one loopback port via the Vast launcher.
# Binds 127.0.0.1:${STABLEDAW_PORT} (default 18600); the Instance Portal's Caddy
# proxy fronts the external port (see 05-stabledaw-env.sh / PORTAL_CONFIG).
cd "${STABLEDAW_DIR}"
STABLEDAW_DIR="${STABLEDAW_DIR}" \
STABLEDAW_HOST="${STABLEDAW_HOST:-127.0.0.1}" \
STABLEDAW_PORT="${STABLEDAW_PORT:-18600}" \
    pty python /opt/stabledaw-serve.py 2>&1
