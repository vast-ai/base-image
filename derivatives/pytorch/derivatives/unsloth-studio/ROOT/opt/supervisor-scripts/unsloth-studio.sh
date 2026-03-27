#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "unsloth"

. /venv/main/bin/activate

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Launch Unsloth Studio
pty unsloth studio ${UNSLOTH_STUDIO_ARGS:---host 127.0.0.1 --port 18888} 2>&1
