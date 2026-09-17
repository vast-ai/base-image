#!/bin/bash

# Wan2GP proxy defaults for AIO Studio (ADR 0042)
#
# Same stage as the standalone wan2gp image; only the default port differs
# (this image launches Wan2GP on 17861, see supervisor-scripts/wan2gp.sh).

# Wan2GP rejects every WebSocket and POST whose Origin is not exactly
# scheme://Host as the app sees it. Behind Caddy that is localhost, so the
# browser's real Origin never matches: Deepy loops on "Connection to server
# lost" and generation requests fail. CADDY_HEADER_UP_LOCALHOST makes Caddy
# send a localhost Host and Origin for the listed ports, so ensure it lists ours.
#
# Parsed the way caddy_config_manager.py parses it: "true" (any case) covers
# every port, anything else is a comma-separated port list. Appending to "true"
# would turn it into a list and narrow it, so "true" is left alone.
wan2gp_port="${WAN2GP_PORT:-17861}"
if [[ -z "${CADDY_HEADER_UP_LOCALHOST:-}" ]]; then
    export CADDY_HEADER_UP_LOCALHOST="${wan2gp_port}"
elif [[ "${CADDY_HEADER_UP_LOCALHOST,,}" != "true" ]]; then
    wan2gp_listed=false
    IFS=',' read -ra wan2gp_ports <<< "${CADDY_HEADER_UP_LOCALHOST}"
    for wan2gp_p in "${wan2gp_ports[@]}"; do
        wan2gp_p="${wan2gp_p#"${wan2gp_p%%[![:space:]]*}"}"
        wan2gp_p="${wan2gp_p%"${wan2gp_p##*[![:space:]]}"}"
        [[ "$wan2gp_p" == "$wan2gp_port" ]] && wan2gp_listed=true
    done
    if [[ "$wan2gp_listed" != "true" ]]; then
        export CADDY_HEADER_UP_LOCALHOST="${CADDY_HEADER_UP_LOCALHOST%,},${wan2gp_port}"
    fi
    unset wan2gp_listed wan2gp_ports wan2gp_p
fi
unset wan2gp_port
