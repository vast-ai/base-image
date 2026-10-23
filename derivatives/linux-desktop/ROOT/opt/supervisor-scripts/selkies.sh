#!/bin/bash

utils=/opt/supervisor-scripts/utils
# Keep out of GUI display - Very noisy
. "${utils}/logging.sh" "/var/log/${PROC_NAME}.log"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/selkies.sh"

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "${PROC_NAME} startup paused until instance provisioning has completed"
    sleep 5
done

socket="$XDG_RUNTIME_DIR/pipewire-0"
echo "Waiting for ${socket}..."
while ! { [[ -S $socket ]] && timeout 1 socat -u OPEN:/dev/null "UNIX-CONNECT:${socket}" 2>/dev/null; }; do
    sleep 1
done

# The transport depends on whether Caddy serves this route over TLS, which Caddy
# decides when it writes this boot's config.
selkies_wait_caddyfile
selkies_plan
selkies_exec
