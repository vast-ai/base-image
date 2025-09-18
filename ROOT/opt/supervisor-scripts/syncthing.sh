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

# We run this as user (uid 1001) because Syncthing displays security warnings if run as root
run_syncthing() {
    API_KEY=${OPEN_BUTTON_TOKEN:-$(openssl rand -hex 16)}
    /opt/syncthing/syncthing generate
    sed -i '/^\s*<listenAddress>/d' /opt/syncthing/config/config.xml
    sed -i 's/<natEnabled>true<\/natEnabled>/<natEnabled>false<\/natEnabled>/' "${STCONFDIR}/config.xml"

    /opt/syncthing/syncthing serve --no-restart --no-browser --gui-address="127.0.0.1:18384" --gui-apikey="${API_KEY}" --no-upgrade 2>&1 &
    syncthing_pid=$!

    until curl --output /dev/null --silent --head --fail "http://127.0.0.1:18384"; do
        echo "Waiting for syncthing server..."
        sleep 1
    done

    # Execute configuration commands with retries
    run_with_retry /opt/syncthing/syncthing cli --gui-address="127.0.0.1:18384" --gui-apikey="${API_KEY}" config gui insecure-admin-access set true 
    run_with_retry /opt/syncthing/syncthing cli --gui-address="127.0.0.1:18384" --gui-apikey="${API_KEY}" config gui insecure-skip-host-check set true
    run_with_retry /opt/syncthing/syncthing cli --gui-address="127.0.0.1:18384" --gui-apikey="${API_KEY}" config options raw-listen-addresses add "tcp://0.0.0.0:${VAST_TCP_PORT_72299}"
    wait $syncthing_pid
}

run_with_retry() {
    until "$@"; do
        echo "Command failed. Retrying..."
        sleep 1
    done
}

run_syncthing
