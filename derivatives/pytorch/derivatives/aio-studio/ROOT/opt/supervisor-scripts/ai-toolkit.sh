#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "AI Toolkit"

echo "Starting AI Toolkit"

. /venv/ostris/bin/activate
. /opt/nvm/nvm.sh

cd "${WORKSPACE}/ai-toolkit/ui"

# AI Toolkit's Node worker spawns run.py training jobs in a new session
# (start_new_session=True), so they detach from the process group and
# survive supervisor stop. Override the cleanup trap to kill them by pattern.
cleanup_ai_toolkit() {
    echo "Stopping AI Toolkit and training jobs..."
    pkill -TERM -f "ai-toolkit/run\.py" 2>/dev/null
    sleep 2
    pkill -KILL -f "ai-toolkit/run\.py" 2>/dev/null
}
trap cleanup_ai_toolkit EXIT INT TERM

pty ${AI_TOOLKIT_START_CMD:-npm run start} 2>&1
