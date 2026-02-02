#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

if [[ -f /run/dbus/pid ]]; then
    kill -9 $(cat /run/dbus/pid)
    rm -f /run/dbus/pid
fi

dbus-daemon --system --nofork
