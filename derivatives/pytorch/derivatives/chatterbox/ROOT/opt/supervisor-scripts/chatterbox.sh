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

# Chatterbox-TTS-Server lives at /opt/chatterbox (internal app dir, like voicebox);
# it reads host/port only from config.yaml — the build patched host to 127.0.0.1
# (loopback, behind Caddy), port 8004. Weights download to model_cache on first synth.
cd /opt/chatterbox

pty python3 server.py
