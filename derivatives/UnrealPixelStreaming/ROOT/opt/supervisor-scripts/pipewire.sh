#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"

while ! pgrep -f "\-\-config-file=/home/user/.config/dbus/session-local.conf" > /dev/null; do
    echo "Waiting for dbus process with local config..."
    sleep 1
done

pipewire 2>&1
