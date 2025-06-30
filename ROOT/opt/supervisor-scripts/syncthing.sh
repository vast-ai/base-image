#!/bin/bash

kill_subprocesses() {
    local pid=$1
    local subprocesses=$(pgrep -P "$pid")
    
    for process in $subprocesses; do
        kill_subprocesses "$process"
    done
    
    if [[ -n "$subprocesses" ]]; then
        kill -TERM $subprocesses 2>/dev/null
    fi
}

cleanup() {
    kill_subprocesses $$
    sleep 2
    pkill -KILL -P $$ 2>/dev/null
    exit 0
}

trap cleanup EXIT INT TERM

set -a
. /etc/environment 2>/dev/null
. ${WORKSPACE}/.env 2>/dev/null
set +a

# User can configure startup by removing the reference in /etc.portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

# Check for $search_term in the portal config
search_term="sync thing"
search_pattern=$(echo "$search_term" | sed 's/[ _-]/[ _-]?/gi')
if ! grep -qiE "^[^#].*${search_pattern}" /etc/portal.yaml; then
    echo "Skipping startup for ${PROC_NAME} (not in /etc/portal.yaml)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    exit 0
fi

# Keep the per-machine settings out of /home/user in case of volume syncing /home
export STCONFDIR=${STCONFDIR:-/opt/syncthing/config}
export STDATADIR=${STDATADIR:-/opt/syncthing/data}

# We run this as user (uid 1001) because Syncthing displays security warnings if run as root
run_syncthing() {
    API_KEY=${OPEN_BUTTON_TOKEN:-$(openssl rand -hex 16)}
    /opt/syncthing/syncthing generate
    sed -i '/^\s*<listenAddress>/d' /opt/syncthing/config/config.xml
    sed -i 's/<natEnabled>true<\/natEnabled>/<natEnabled>false<\/natEnabled>/' "${STCONFDIR}/config.xml"

    /opt/syncthing/syncthing serve --no-restart --no-browser --no-default-folder --gui-address="127.0.0.1:18384" --gui-apikey="${API_KEY}" --no-upgrade &
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

run_syncthing 2>&1 | tee -a "/var/log/portal/${PROC_NAME}.log"
