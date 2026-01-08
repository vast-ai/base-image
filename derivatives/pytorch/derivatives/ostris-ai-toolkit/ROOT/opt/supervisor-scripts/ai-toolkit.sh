#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "AI Toolkit"

echo "Starting AI Toolkit"

. /venv/main/bin/activate
. /opt/nvm/nvm.sh

cd "${WORKSPACE}/ai-toolkit/ui"

${AI_TOOLKIT_START_CMD:-npm run start} 2>&1
