#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Whisper WebUI"

. /venv/whisper/bin/activate

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 5
done

echo "Starting Whisper WebUI"

cd "${WORKSPACE}/Whisper-WebUI"
pty python app.py ${WHISPER_UI_ARGS:---whisper_type whisper --server_port 7862} 2>&1
