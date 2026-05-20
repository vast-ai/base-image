#!/bin/bash

# Strip the Selkies "Desktop" entry (internal port 16100) when the binary
# isn't installed — upstream only ships amd64 release artifacts, so ARM64
# images build without it. Filtering by port keeps this robust against
# entry-name variations in operator-supplied PORTAL_CONFIG templates.
# Tomcat + Guacamole VNC (port 16200) remains the remote-access path.
if [[ -n "$PORTAL_CONFIG" ]] && ! command -v selkies-gstreamer >/dev/null 2>&1; then
    PORTAL_CONFIG=$(echo "$PORTAL_CONFIG" | tr '|' '\n' | grep -v ':16100:' | tr '\n' '|' | sed 's/|$//')
fi
