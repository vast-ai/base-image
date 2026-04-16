#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "ACE Step"

. /venv/ace-step/bin/activate

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 5
done

export ACESTEP_LM_MODEL_PATH=${ACESTEP_LM_MODEL_PATH:=acestep-5Hz-lm-4B}

# Start ACE Step API in background
echo "Starting ACE Step API..."
cd "${WORKSPACE}/ACE-Step-1.5"
(pty acestep-api --port 8001) &
API_PID=$!
trap "kill -- -$API_PID 2>/dev/null" EXIT

# Wait for ACE Step API to be ready
until (curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/docs || echo "000") | grep -q 200; do
    # Check if API process is still alive
    if ! kill -0 $API_PID 2>/dev/null; then
        echo "ACE Step API process died unexpectedly"
        exit 1
    fi
    echo "Waiting for ACE Step API..."
    sleep 5
done
echo "ACE Step API is up!"

# Start ACE Step UI (foreground)
echo "Starting ACE Step UI"
cd "${WORKSPACE}/ace-step-ui"
. /opt/nvm/nvm.sh
ACESTEP_PATH="${WORKSPACE}/ACE-Step-1.5/" pty ./start.sh
