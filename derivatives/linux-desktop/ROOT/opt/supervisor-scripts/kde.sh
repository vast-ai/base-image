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

VGL_PRELOAD="/etc/ld.so.preload"

cleanup_vgl() {
    rm -f "$VGL_PRELOAD"
}
trap cleanup_vgl EXIT

# Start KDE without VGL.  VGL's GL interposition on kwin/plasmashell
# breaks the desktop on some GPU configurations (e.g. compute-only GPUs).
/usr/bin/startplasma-x11 &
KDE_PID=$!

# After KDE's own processes are running, enable VGL for user applications.
# /etc/ld.so.preload is read by the dynamic linker at exec() time, so
# already-running KDE components are unaffected but every newly launched
# app (menus, terminals, .desktop files) gets GPU acceleration via VGL
# automatically — no vglrun wrapper needed.
if [ -n "$(nvidia-smi --query-gpu=uuid --format=csv,noheader 2>/dev/null | head -n1)" ] || [ -n "$(ls -A /dev/dri 2>/dev/null)" ]; then
    sleep 3
    echo '/usr/$LIB/libvglfaker.so' > "$VGL_PRELOAD"
    echo "VGL acceleration enabled for user applications"
fi

wait $KDE_PID
