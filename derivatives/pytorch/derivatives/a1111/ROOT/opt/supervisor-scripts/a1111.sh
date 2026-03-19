#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "AUTOMATIC1111"

. /venv/main/bin/activate

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

export GIT_CONFIG_GLOBAL=/tmp/temporary-git-config
git config --file $GIT_CONFIG_GLOBAL --add safe.directory '*'

# Launch A1111
cd ${DATA_DIRECTORY}stable-diffusion-webui
LD_PRELOAD=libtcmalloc_minimal.so.4 \
        pty python launch.py \
        ${A1111_ARGS:---port 17860} 2>&1
