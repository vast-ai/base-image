#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Whisper WebUI"

. /venv/main/bin/activate

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Wait for the Whisper API to be ready so the UI backend calls succeed on first load
WHISPER_API_HEALTH_URL=${WHISPER_API_HEALTH_URL:-http://localhost:8000/docs}
until (curl -s -o /dev/null -w '%{http_code}' "${WHISPER_API_HEALTH_URL}" || echo "000") | grep -q 200; do
    echo "Waiting for Whisper API at ${WHISPER_API_HEALTH_URL}..."
    sleep 5
done
echo "Whisper API is up!"

echo "Starting Whisper WebUI"

cd "${WORKSPACE}/Whisper-WebUI"

pty python app.py ${WHISPER_UI_ARGS:---whisper_type whisper --server_port 7860} 2>&1
