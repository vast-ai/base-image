#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "ComfyUI"

# Activate the venv
. /venv/main/bin/activate

COMFYUI_DIR=${WORKSPACE}/ComfyUI

# Not first boot - Do this to handle frontend being out of sync after manager update
if [[ ! -f /.provisioning ]]; then
    cd "${COMFYUI_DIR}"
    uv pip --no-cache-dir install -r requirements.txt
fi

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 5
done

# Launch ComfyUI
cd "${COMFYUI_DIR}"
LD_PRELOAD=libtcmalloc_minimal.so.4 \
        python main.py \
        ${COMFYUI_ARGS:---disable-auto-launch --port 18188 --enable-cors-header} 2>&1

