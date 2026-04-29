#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Wan2GP"

echo "Starting Wan2GP"

. /venv/main/bin/activate

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Required for SDL audio (Wan2GP imports pygame/SDL on startup)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/tmp}
export SDL_AUDIODRIVER=${SDL_AUDIODRIVER:-dummy}

cd "${WORKSPACE}/Wan2GP"

pty python wgp.py --server-port ${WAN2GP_PORT:-7860} ${WAN2GP_ARGS:-} 2>&1
