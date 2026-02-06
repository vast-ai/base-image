#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    echo "${PROC_NAME} startup paused until instance provisioning has completed"
    sleep 5
done

sleep 2

socket="/tmp/.X11-unix/X${DISPLAY#*:}"
echo "Waiting for ${socket}..."
while ! { [[ -S $socket ]] && timeout 1 socat -u OPEN:/dev/null "UNIX-CONNECT:${socket}" 2>/dev/null; }; do
  sleep 1
done

# Create Guacamole config
cat > /etc/guacamole/noauth-config.xml << EOF
<configs>
    <config name="VNC Desktop" protocol="vnc">
        <param name="hostname" value="localhost" />
        <param name="port" value="5900" />
        <param name="password" value="${VNC_PASSWORD:-$OPEN_BUTTON_TOKEN}" />
    </config>
</configs>
EOF

/opt/tomcat9/bin/catalina.sh run
