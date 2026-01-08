#!/bin/bash

# Let the 'user' account connect via SSH
if [[ "${propagate_user_keys}" = "true" ]]; then
    /opt/instance-tools/bin/propagate_ssh_keys.sh
fi
