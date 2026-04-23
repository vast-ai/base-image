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

# Only wait for the API when it's configured in the portal; otherwise run standalone
# so the documented "UI-only" mode (remove "Whisper API" from /etc/portal.yaml) works.
PORTAL_CONFIG_PATH=${PORTAL_CONFIG_PATH:-/etc/portal.yaml}
WHISPER_API_HEALTH_URL=${WHISPER_API_HEALTH_URL:-http://localhost:8000/docs}
if [ -f "${PORTAL_CONFIG_PATH}" ] && grep -qiE '^[^#].*Whisper[ _-]API' "${PORTAL_CONFIG_PATH}"; then
    until (curl -s -o /dev/null -w '%{http_code}' "${WHISPER_API_HEALTH_URL}" || echo "000") | grep -q 200; do
        echo "Waiting for Whisper API at ${WHISPER_API_HEALTH_URL}..."
        sleep 5
    done
    echo "Whisper API is up!"
else
    echo "Whisper API not in ${PORTAL_CONFIG_PATH}; skipping API readiness wait (UI-only mode)"
fi

echo "Starting Whisper WebUI"

cd "${WORKSPACE}/Whisper-WebUI"

pty python app.py ${WHISPER_UI_ARGS:---whisper_type whisper --server_port 7860} 2>&1
