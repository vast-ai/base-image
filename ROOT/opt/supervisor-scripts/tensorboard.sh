#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"
. "${utils}/exit_portal.sh" "tensorboard"

cd "${WORKSPACE}"
tensorboard --port 16006 --logdir "${TENSORBOARD_LOG_DIR:-${WORKSPACE:-/workspace}}" 2>&1