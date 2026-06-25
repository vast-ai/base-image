#!/bin/bash

# Default portal configuration. The launch template overrides PORTAL_CONFIG;
# this baked default lets the image self-describe its ports (ADR 0002).
#
# Format: localhost:<external>:<internal>:<path>:<Label>  (pipe-separated;
# parsed by portal-aio/caddy_manager/caddy_config_manager.py). Caddy stands up
# an authed proxy site only when external != internal, so the app binds the
# INTERNAL port (18600) on loopback and users reach StableDAW via the EXTERNAL
# port (8600, which the Dockerfile EXPOSEs).
if [[ -z $PORTAL_CONFIG ]]; then
    export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:8600:18600:/:StableDAW|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal"
fi
