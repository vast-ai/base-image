#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "ollama"

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Ensure models and config are saved in the workspace
if [[ ! -L /root/.ollama ]]; then
    ln -s ${WORKSPACE}/ollama /root/.ollama
fi

pty ollama serve ${OLLAMA_ARGS} 2>&1
