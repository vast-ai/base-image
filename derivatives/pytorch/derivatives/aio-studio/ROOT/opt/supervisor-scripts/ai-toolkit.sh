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

# Run via background+wait so cleanup_generic.sh can kill orphan training
# processes (run.py) that escape the process group.
# Cannot use the pty shell function in a subshell, so inline the logic.
CMD="${AI_TOOLKIT_START_CMD:-npm run start}"
$CMD 2>&1 &
wait $!
