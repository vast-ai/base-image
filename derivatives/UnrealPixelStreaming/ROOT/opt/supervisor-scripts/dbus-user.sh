#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"

while [[ ! -S /run/dbus/system_bus_socket ]]; do
    echo "Waiting for system dbus socket..."
    sleep 1
done

dbus-daemon --config-file=/home/user/.config/dbus/session-local.conf --nofork 2>&1
