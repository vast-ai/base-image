#!/bin/bash

if [[ -n $APT_PACKAGES ]]; then
    echo "Installing additional apt packages"
    # Jupyter/SSH mode will handle the package updates
    apt-cache pkgnames | head -1 | grep -q . || apt-get update
    apt-get install --no-install-recommends -y $APT_PACKAGES
fi

if [[ -n $PIP_PACKAGES ]]; then
    uv pip install --python /venv/main/bin/python $PIP_PACKAGES
fi

# Put anything more complex into a PROVISIONING_SCRIPT
