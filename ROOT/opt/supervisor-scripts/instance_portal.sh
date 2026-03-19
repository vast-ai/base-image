#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"
. "${utils}/exit_portal.sh" "instance portal"

cd /opt/portal-aio/portal
pty /opt/portal-aio/venv/bin/fastapi run --host 127.0.0.1 --port 11111 portal.py 2>&1
