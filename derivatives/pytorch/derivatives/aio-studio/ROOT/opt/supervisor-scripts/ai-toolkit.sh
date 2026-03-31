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

# Run via background+wait so the shell stays alive and cleanup_generic.sh
# can kill child processes that escape the process group (e.g. run.py
# training jobs spawned by the Node worker with start_new_session=True)
pty ${AI_TOOLKIT_START_CMD:-npm run start} 2>&1 &
wait $!
