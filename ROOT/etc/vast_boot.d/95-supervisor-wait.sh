#!/bin/bash

# Remove the blocker and leave supervisord to run
rm -f /.provisioning

if [[ -n "${supervisord_pid:-}" ]]; then
    wait "$supervisord_pid"
else
    # 65-supervisor-launch.sh found nowhere to put supervisord's control socket
    # and did not start it. This is the container's main process: returning
    # would stop the container, so stay up and keep SSH and Jupyter reachable.
    echo "[supervisor-wait] supervisord was not started (see [supervisor-launch] above); keeping the container up"
    while :; do sleep 3600; done
fi
