#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

mkdir -p "${XDG_RUNTIME_DIR}/dbus"

socket="/run/dbus/system_bus_socket"
echo "Waiting for ${socket}..."
while ! { [[ -S $socket ]] && timeout 1 socat -u OPEN:/dev/null "UNIX-CONNECT:${socket}" 2>/dev/null; }; do
  sleep 1
done

dbus-daemon --config-file=/etc/dbus-1/container-session.conf --nofork
