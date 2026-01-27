#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Forge"

# Activate the venv
. /venv/main/bin/activate

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Launch Forge
cd ${WORKSPACE}/stable-diffusion-webui-forge
LD_PRELOAD=libtcmalloc_minimal.so.4 \
        python launch.py \
        ${FORGE_ARGS:---port 17860}

