#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

# Always hide from portal (never show in supervisor view)
mkdir -p /tmp/supervisor-skip
touch "/tmp/supervisor-skip/${PROC_NAME}"

# Only run in serverless mode
if [[ "${SERVERLESS,,}" != "true" ]]; then
    echo "Skipping ${PROC_NAME} startup (not serverless)"
    sleep 6
    exit 0
fi

# Skip if onstart.sh already handles pyworker bootstrap
bootstrap_url="https://raw.githubusercontent.com/vast-ai/pyworker/refs/heads/main/start_server.sh"
if [[ -f /root/onstart.sh ]] && grep -qF "$bootstrap_url" /root/onstart.sh; then
    echo "Skipping ${PROC_NAME} startup (handled by /root/onstart.sh)"
    sleep 6
    exit 0
fi

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "${PROC_NAME} startup paused (provisioning)..."
    sleep 5
done

# Launch pyworker via bootstrap
curl -L "$bootstrap_url" | bash
