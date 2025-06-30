#!/bin/bash

trap 'ray stop' EXIT

# User can configure startup by removing the reference in /etc.portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

if [[ -z "${VLLM_MODEL:-}" ]]; then
    echo "Skipping startup for ${PROC_NAME} (\$VLLM_MODEL not specified)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    exit 0
fi

# Check for ray in the portal config
search_term="ray dash"
search_pattern=$(echo "$search_term" | sed 's/[ _-]/[ _-]/g')
if ! grep -qiE "^[^#].*${search_pattern}" /etc/portal.yaml; then
    echo "Skipping startup for ${PROC_NAME} (not in /etc/portal.yaml)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    exit 0
fi

# Activate the venv
. /venv/main/bin/activate

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Launch Ray
cd ${WORKSPACE}
ray start ${RAY_ARGS:---head --dashboard-host 127.0.0.1 --dashboard-port 28265} 2>&1 | tee -a "/var/log/portal/${PROC_NAME}.log"

sleep infinity
