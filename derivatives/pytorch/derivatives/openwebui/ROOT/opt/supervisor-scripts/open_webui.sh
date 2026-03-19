#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "open webui"

. /venv/main/bin/activate

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Launch Open Webui
cd ${WORKSPACE}

[[ -z $WEBUI_SECRET_KEY ]] && export WEBUI_SECRET_KEY="${OPEN_BUTTON_TOKEN}"

[[ -z $OLLAMA_BASE_URL ]] && export OLLAMA_BASE_URL="http://127.0.0.1:21434"

[[ -z $OPENAI_API_BASE_URL ]] && export OPENAI_API_BASE_URL="http://127.0.0.1:20000"

[[ -z $OPENAI_API_KEY ]] && export OPENAI_API_KEY="${OPEN_BUTTON_TOKEN:-none}"

export DATA_DIR="${DATA_DIR:-${WORKSPACE:-/workspace}/webui}"
pty open-webui serve ${OPENWEBUI_ARGS:---host 127.0.0.1 --port 17500}
