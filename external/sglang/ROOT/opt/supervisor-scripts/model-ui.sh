#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
[[ "${SERVERLESS:-false}" = "false" ]] && . "${utils}/exit_portal.sh" "model ui"

# No model = no UI
if [[ -z "${MODEL_NAME:-}" ]]; then
    echo "Skipping ${PROC_NAME} startup (MODEL_NAME not set)"
    exit 0
fi

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

/opt/model-ui/venv/bin/python /opt/model-ui/app.py
