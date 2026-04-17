#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Wan2GP"

echo "Starting Wan2GP"

. /venv/wan2gp/bin/activate

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 5
done

cd "${WORKSPACE}/Wan2GP"
export XDG_RUNTIME_DIR=/tmp
export SDL_AUDIODRIVER=dummy
pty python wgp.py --server-port ${WAN2GP_PORT:-17861} 2>&1
