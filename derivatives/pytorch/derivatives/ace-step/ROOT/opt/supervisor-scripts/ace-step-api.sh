#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "ACE Step API"

. /venv/main/bin/activate

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 5
done

echo "Starting ACE Step API"

cd "${WORKSPACE}/ACE-Step-1.5"
UV_PROJECT_ENVIRONMENT=/venv/main uv run acestep-api --port 8001
