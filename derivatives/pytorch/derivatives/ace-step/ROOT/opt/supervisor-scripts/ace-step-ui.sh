#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "ACE Step UI"

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 5
done

export ACESTEP_LM_MODEL_PATH=${ACESTEP_LM_MODEL_PATH:=acestep-5Hz-lm-4B}

# Wait for ACE Step API to be ready
until (curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/docs || echo "000") | grep -q 200; do
    echo "Waiting for ACE Step API..."
    sleep 5
done
echo "ACE Step API is up!"

echo "Starting ACE Step UI"

cd "${WORKSPACE}/ace-step-ui"
. /opt/nvm/nvm.sh
ACESTEP_PATH="${WORKSPACE}/ACE-Step-1.5/" ./start.sh
