#!/bin/bash

# Ports whose app needs Caddy's localhost rewrite (ADR 0043).
# Wan2GP: its launcher runs wgp.py on ${WAN2GP_PORT:-17861}.
# ACE Step UI: the vite frontend port, fixed in ace-step-ui's vite.config.ts
# (asserted in the Dockerfile).
caddy_localhost_ports=("${WAN2GP_PORT:-17861}" 3000)

# ---- shared body: identical in every image (ADR 0043) ----
# Some apps reject a WebSocket or POST whose Origin is not exactly scheme://Host
# as the app sees it. Behind Caddy that Host is localhost, so a browser's real
# Origin never matches. CADDY_HEADER_UP_LOCALHOST makes Caddy send a localhost
# Host and Origin for the listed internal ports; ensure it lists ours, keeping
# whatever the template already set.
#
# Parsed the way caddy_config_manager.py parses it: "true" (any case) covers
# every port, anything else is a comma-separated port list. Appending to "true"
# would turn it into a list and narrow it, so "true" is left alone.
if [[ "${CADDY_HEADER_UP_LOCALHOST,,}" != "true" ]]; then
    for caddy_localhost_port in "${caddy_localhost_ports[@]}"; do
        caddy_localhost_listed=false
        IFS=',' read -ra caddy_localhost_entries <<< "${CADDY_HEADER_UP_LOCALHOST:-}"
        for caddy_localhost_entry in "${caddy_localhost_entries[@]}"; do
            caddy_localhost_entry="${caddy_localhost_entry#"${caddy_localhost_entry%%[![:space:]]*}"}"
            caddy_localhost_entry="${caddy_localhost_entry%"${caddy_localhost_entry##*[![:space:]]}"}"
            [[ "$caddy_localhost_entry" == "$caddy_localhost_port" ]] && caddy_localhost_listed=true
        done
        if [[ "$caddy_localhost_listed" != "true" ]]; then
            if [[ -z "${CADDY_HEADER_UP_LOCALHOST:-}" ]]; then
                export CADDY_HEADER_UP_LOCALHOST="$caddy_localhost_port"
            else
                export CADDY_HEADER_UP_LOCALHOST="${CADDY_HEADER_UP_LOCALHOST%,},$caddy_localhost_port"
            fi
        fi
    done
fi
unset caddy_localhost_ports caddy_localhost_port caddy_localhost_listed caddy_localhost_entries caddy_localhost_entry
# ---- end shared body ----
