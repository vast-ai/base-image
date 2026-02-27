#!/bin/bash

if [[ -n $APT_PACKAGES ]]; then
    echo "Installing additional apt packages"
    # Only pull package updates if not using Jupyter or SSH launch
    [[ ! -f /.launch ]] && apt-get update
    apt-get install --no-install-recommends -y $APT_PACKAGES
fi

if [[ -n $PIP_PACKAGES ]]; then
    echo "Installing additional python packages"
    uv pip install --system $PIP_PACKAGES
fi

# Put anything more complex into a PROVISIONING_SCRIPT
