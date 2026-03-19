#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "oobabooga"

. /venv/main/bin/activate

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

export GIT_CONFIG_GLOBAL=/tmp/temporary-git-config
git config --file $GIT_CONFIG_GLOBAL --add safe.directory '*'

# Launch Oobabooga
cd ${DATA_DIRECTORY}text-generation-webui
LD_PRELOAD=libtcmalloc_minimal.so.4 \
        pty python server.py \
        ${OOBABOOGA_ARGS:---listen-port 17860 --api --api-port 15000} 2>&1
