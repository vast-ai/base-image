#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "ray dash"

# Activate the venv
. /venv/main/bin/activate

trap 'ray stop' EXIT

# Check we are actually trying to serve a model
if [[ -z "${VLLM_MODEL:-}" ]]; then
    echo "Refusing to start ${PROC_NAME} (VLLM_MODEL not set)"
    sleep 6
    exit 0
fi

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Launch Ray
cd ${WORKSPACE}

ray start ${RAY_ARGS:---head --port 6379  --dashboard-host 127.0.0.1 --dashboard-port 28265} 2>&1

sleep infinity