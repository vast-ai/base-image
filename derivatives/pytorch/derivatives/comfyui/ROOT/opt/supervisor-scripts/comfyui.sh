#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

# Serverless
if [[ "${SERVERLESS:-false}" != "true" ]]; then
    . "${utils}/exit_portal.sh" "ComfyUI"
fi

COMFYUI_DIR=${WORKSPACE}/ComfyUI

# Activate the venv
. /venv/main/bin/activate

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

COMFYUI_ARGS=${COMFYUI_ARGS:---disable-auto-launch --port 18188 --enable-cors-header}

# Currently xformers will not work on Blackwell so forcefully disable it on that architecture
if [[ "${BLACKWELL_ALLOW_XFORMERS:-false}" != "true" ]]; then
    if nvidia-smi -q | grep -qi blackwell && ! echo "$COMFYUI_ARGS" | grep -qe --disable-xformers; then
        COMFYUI_ARGS="$COMFYUI_ARGS --disable-xformers"
    fi
fi


# Launch ComfyUI
cd "${COMFYUI_DIR}"
LD_PRELOAD=libtcmalloc_minimal.so.4 \
        python main.py \
        ${COMFYUI_ARGS} 2>&1

