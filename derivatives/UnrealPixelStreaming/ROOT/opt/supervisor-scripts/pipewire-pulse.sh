#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"

while ! pgrep -f "wireplumber" > /dev/null; do
    echo "Waiting for wireplumber..."
    sleep 1
done

pipewire-pulse 2>&1
