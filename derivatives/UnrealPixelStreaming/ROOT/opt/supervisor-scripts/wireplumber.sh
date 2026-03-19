#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"

while [[ ! -S /run/user/1001/pipewire-0 ]]; do
    echo "Waiting for pipewire socket..."
    sleep 1
done

wireplumber 2>&1
