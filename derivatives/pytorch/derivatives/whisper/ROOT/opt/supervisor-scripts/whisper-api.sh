#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Whisper API"

echo "Starting Whisper API"

. /venv/main/bin/activate

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

cd "${WORKSPACE}/Whisper-WebUI"

pty uvicorn backend.main:app ${WHISPER_API_ARGS:---host 0.0.0.0 --port 8000} 2>&1
