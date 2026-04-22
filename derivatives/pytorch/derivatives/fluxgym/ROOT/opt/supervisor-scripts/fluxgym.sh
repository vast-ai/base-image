#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Flux Gym"

echo "Starting Flux Gym"

. /venv/main/bin/activate

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

cd "${WORKSPACE}/fluxgym"

GRADIO_SERVER_PORT=${FLUXGYM_PORT:-17860} pty python app.py 2>&1
