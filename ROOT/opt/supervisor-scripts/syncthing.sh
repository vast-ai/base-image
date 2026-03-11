#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"
. "${utils}/exit_portal.sh" "syncthing"

# Keep the per-machine settings out of /home/user in case of volume syncing /home
export STCONFDIR=${STCONFDIR:-/opt/syncthing/config}
export STDATADIR=${STDATADIR:-/opt/syncthing/data}

GUI_ADDR="127.0.0.1:18384"
API_KEY=${OPEN_BUTTON_TOKEN:-$(openssl rand -hex 16)}
CLI="/opt/syncthing/syncthing cli --gui-address=${GUI_ADDR} --gui-apikey=${API_KEY}"

run_with_retry() {
    local max_attempts=${MAX_RETRY:-30}
    local attempt=0
    until "$@"; do
        attempt=$((attempt + 1))
        if [[ $attempt -ge $max_attempts ]]; then
            echo "Command failed after ${max_attempts} attempts: $*"
            return 1
        fi
        sleep 1
    done
}

# Remove stale lock files in case a previous instance was force-killed
find "${STCONFDIR}" "${STDATADIR}" -name "LOCK" -delete 2>/dev/null

# Only generate config/certs on first run
if [[ ! -f "${STCONFDIR}/config.xml" ]]; then
    /opt/syncthing/syncthing generate
    # Apply initial configuration
    sed -i '/^\s*<listenAddress>/d' "${STCONFDIR}/config.xml"
    sed -i 's/<natEnabled>true<\/natEnabled>/<natEnabled>false<\/natEnabled>/' "${STCONFDIR}/config.xml"
fi

pty /opt/syncthing/syncthing serve \
    --no-restart \
    --no-browser \
    --gui-address="${GUI_ADDR}" \
    --gui-apikey="${API_KEY}" \
    --no-upgrade 2>&1 &
syncthing_pid=$!

# Wait for the GUI to become available
if ! run_with_retry curl --output /dev/null --silent --head --fail "http://${GUI_ADDR}"; then
    echo "Syncthing failed to start"
    exit 1
fi

# Apply runtime configuration (idempotent set operations)
run_with_retry $CLI config gui insecure-admin-access set true
run_with_retry $CLI config gui insecure-skip-host-check set true
# Use 'set' instead of 'add' to avoid duplicates on restart
run_with_retry $CLI config options raw-listen-addresses set "tcp://0.0.0.0:${VAST_TCP_PORT_72299}" "dynamic+https://relays.syncthing.net/endpoint"

wait $syncthing_pid
