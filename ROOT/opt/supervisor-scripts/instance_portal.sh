#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh" "/var/log/${PROC_NAME}.log"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"
. "${utils}/exit_portal.sh" "instance portal"

cd /opt/portal-aio/portal
# Log outside of /var/log/portal
/opt/portal-aio/venv/bin/fastapi run --host 127.0.0.1 --port 11111 portal.py 2>&1
