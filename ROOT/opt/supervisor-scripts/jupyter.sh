#!/bin/bash

# Only runs if 'Jupyter' is found in Portal configuration - Otherwise we let /.launch run as normal
# This method of startup is useful because:
# 1) It's user-configurable
# 2) it uses our venv's python binary by default
# 3) We get unauthenticated access without TLS via SSH forwarding 
# 4) it gives us a shell in Args runtype

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"


if [[ -f /.launch ]] && grep -qi jupyter /.launch && [[ "${JUPYTER_OVERRIDE,,}" != "true" ]]; then
    echo "Refusing to start ${PROC_NAME} (/.launch managing)"
    sleep 6
    exit 0
fi

. "${utils}/exit_portal.sh" "jupyter"

# Required for default jupyter override
pgrep -f "jupyter-lab|jupyter-notebook|jupyter notebook" | xargs -r kill -9 > /dev/null 2>&1

type="${JUPYTER_TYPE:-notebook}"

# Ensure the default Python used by Jupyter is our venv
# Token not specified because auth is handled through Caddy
cd ${WORKSPACE}
"${JUPYTER_BIN:-/venv/main/bin/jupyter}" "${type,,}" \
        --allow-root \
        --ip=127.0.0.1 \
        --port=18080 \
        --no-browser \
        --IdentityProvider.token='' \
        --ServerApp.password='' \
        --ServerApp.trust_xheaders=True \
        --ServerApp.disable_check_xsrf=False \
        --ServerApp.allow_remote_access=True \
        --ServerApp.allow_origin='*' \
        --ServerApp.allow_credentials=True \
        --ServerApp.root_dir=/ \
        --ServerApp.preferred_dir="$DATA_DIRECTORY" \
        --ServerApp.terminado_settings="{'shell_command': ['/bin/bash']}" \
        --ContentsManager.allow_hidden=True \
        --KernelSpecManager.ensure_native_kernel=False 2>&1
