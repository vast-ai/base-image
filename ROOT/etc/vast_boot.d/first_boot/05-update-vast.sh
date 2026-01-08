#!/bin/bash

if [[ "${update_vast_cli}" = "true" ]]; then
    echo "Updating Vast.ai CLI tool"
    (cd /opt/vast-cli && git pull > /dev/null 2>&1)
fi
