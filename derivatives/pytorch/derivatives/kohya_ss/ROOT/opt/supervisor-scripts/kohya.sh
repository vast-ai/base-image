#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"
. "${utils}/exit_portal.sh" "kohya"


# Activate the venv
. /venv/main/bin/activate

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)" 
    sleep 10
done

# Launch Kohya's GUI
cd ${WORKSPACE}/kohya_ss
LD_PRELOAD=libtcmalloc_minimal.so.4 \
        python kohya_gui.py \
        ${KOHYA_ARGS:---server_port 17860 --headless --noverify} 2>&1
