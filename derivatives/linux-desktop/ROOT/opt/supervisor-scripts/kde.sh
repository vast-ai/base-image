#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

socket="$XDG_RUNTIME_DIR/pipewire-0"
echo "Waiting for ${socket}..."
while ! { [[ -S $socket ]] && timeout 1 socat -u OPEN:/dev/null "UNIX-CONNECT:${socket}" 2>/dev/null; }; do
    sleep 1
done

export XDG_SESSION_ID="${DISPLAY#*:}"
export QT_LOGGING_RULES="${QT_LOGGING_RULES:-*.debug=false;qt.qpa.*=false}"
export SHELL=${SHELL:-/bin/bash}

# The desktop's applications reach the client's camera and gamepads through Selkies'
# interposers, inherited from the session (ADR 0050).
. "${utils}/selkies.sh"
export LD_PRELOAD="$(selkies_session_preload)"

/usr/bin/startplasma-x11
