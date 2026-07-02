#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

# Serverless: skip the portal-config gate when running serverless
if [[ "${SERVERLESS:-false}" != "true" ]]; then
    . "${utils}/exit_portal.sh" "Chatterbox"
fi

[[ -f /venv/main/bin/activate ]] && . /venv/main/bin/activate

# Wait for provisioning to complete before starting
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until provisioning completes (/.provisioning present)"
    sleep 5
done

# Chatterbox-TTS-Server is built into /opt/workspace-internal/chatterbox and migrated to
# $WORKSPACE/chatterbox on first boot (volume-backed, so model_cache persists across
# restarts). It reads host/port only from config.yaml — the build patched host to
# 127.0.0.1 (loopback, behind Caddy), port 8004. Weights download to model_cache on first synth.
cd "${WORKSPACE}/chatterbox" 2>/dev/null || cd "${WORKSPACE}"

pty python3 server.py
