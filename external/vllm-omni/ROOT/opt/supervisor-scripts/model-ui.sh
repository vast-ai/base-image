#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "model ui"

# Env var gate
if [[ "${ENABLE_UI,,}" = "false" ]]; then
    echo "Skipping ${PROC_NAME} startup (ENABLE_UI=false)"
    exit 0
fi

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

python3 /opt/model-ui/app.py
