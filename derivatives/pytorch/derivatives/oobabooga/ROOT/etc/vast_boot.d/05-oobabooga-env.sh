#!/bin/bash

# Default portal configuration (ADR 0002). The launch template overrides
# PORTAL_CONFIG; this baked default lets the image self-describe its ports.
#
# Format: localhost:<external>:<internal>:<path>:<Label>  (pipe-separated;
# parsed by portal-aio/caddy_manager/caddy_config_manager.py). Caddy stands up
# an authed proxy site only when external != internal, so each app binds its
# INTERNAL port on loopback (server.py without --listen; see oobabooga.sh) and
# users reach it via the EXTERNAL port (opened by the Vast template's `ports:` config).
#   Text Generation WebUI: external 7860  -> internal 17860
#   Oobabooga API (OpenAI-compatible): external 5000 -> internal 15000
if [[ -z $PORTAL_CONFIG ]]; then
    export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal|localhost:7860:17860:/:Text Generation WebUI|localhost:5000:15000:/:Oobabooga API"
fi
