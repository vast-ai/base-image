#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

# Serverless
if [[ "${SERVERLESS:-false}" != "true" ]]; then
    . "${utils}/exit_portal.sh" "API Wrapper"
fi

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 5
done

# Launch ComfyUI API Wrapper
cd /opt/comfyui-api-wrapper
. .venv/bin/activate

uvicorn main:app --port 18288 2>&1
