#!/bin/bash

# Enable autostart for selected supervisor services.
# Usage: SUPERVISOR_AUTOSTART=comfyui,forge,unsloth-studio
# Service names must match [program:NAME] in /etc/supervisor/conf.d/*.conf
[[ -z "${SUPERVISOR_AUTOSTART}" ]] && return 0

IFS=',' read -ra services <<< "${SUPERVISOR_AUTOSTART}"
for svc in "${services[@]}"; do
    svc="${svc// /}"  # trim whitespace
    conf="/etc/supervisor/conf.d/${svc}.conf"
    if [[ -f "${conf}" ]]; then
        sed -i 's/^autostart=false/autostart=true/' "${conf}"
        echo "Enabled autostart for ${svc}"
    else
        echo "Warning: no supervisor config found for '${svc}' (expected ${conf})" >&2
    fi
done
