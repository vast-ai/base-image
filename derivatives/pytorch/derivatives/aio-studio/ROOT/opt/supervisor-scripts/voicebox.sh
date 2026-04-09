#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Voicebox"

VOICEBOX_DIR=/opt/voicebox

. /venv/voicebox/bin/activate

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 5
done

# Ensure data directory exists on workspace volume
mkdir -p "${VOICEBOX_DATA_DIR:-${WORKSPACE}/voicebox-data}"

VOICEBOX_ARGS=${VOICEBOX_ARGS:---host 127.0.0.1 --port 17493}

# Launch Voicebox
cd "${VOICEBOX_DIR}"
pty python -m backend.main \
    --data-dir "${VOICEBOX_DATA_DIR:-${WORKSPACE}/voicebox-data}" \
    ${VOICEBOX_ARGS} 2>&1
