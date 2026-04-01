#!/bin/bash
#
# Unified desktop service — starts the full GPU-accelerated remote desktop
# stack as a single supervisor process. Stopping this service tears down
# everything: display server, VNC, Selkies streaming, KDE, audio, etc.
#
# Dependency chain:
#   dbus-system → dbus-session → x-server → pipewire → kde + selkies + vnc
#

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

PIDS=()

log() { echo "[desktop] $*"; }

# Track a background process for cleanup
run_bg() {
    local name=$1; shift
    log "Starting $name..."
    "$@" 2>&1 | sed -u "s/^/[$name] /" &
    PIDS+=($!)
    log "$name started (PID $!)"
}

# Run as the 'user' account (uid 1001) — desktop apps should not run as root
run_bg_user() {
    local name=$1; shift
    log "Starting $name (as user)..."
    runuser -u user -- "$@" 2>&1 | sed -u "s/^/[$name] /" &
    PIDS+=($!)
    log "$name started (PID $!)"
}

# Wait for a unix socket to become available
wait_socket() {
    local socket=$1 label=$2 elapsed=0
    log "Waiting for $label..."
    while ! { [[ -S $socket ]] && timeout 1 socat -u OPEN:/dev/null "UNIX-CONNECT:${socket}" 2>/dev/null; }; do
        sleep 0.5
        elapsed=$((elapsed + 1))
        if (( elapsed % 20 == 0 )); then
            log "Still waiting for $label (${elapsed}s)..."
        fi
    done
    log "$label ready"
}

wait_file() {
    local path=$1 label=$2 elapsed=0
    log "Waiting for $label..."
    while [[ ! -e $path ]]; do
        sleep 0.5
        elapsed=$((elapsed + 1))
        if (( elapsed % 20 == 0 )); then
            log "Still waiting for $label (${elapsed}s)..."
        fi
    done
    log "$label ready"
}

# --- Cleanup ---
cleanup_desktop() {
    log "Shutting down desktop stack..."
    for (( i=${#PIDS[@]}-1; i>=0; i-- )); do
        kill -TERM "${PIDS[$i]}" 2>/dev/null
    done
    sleep 2
    for pid in "${PIDS[@]}"; do
        kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
    done
    rm -f /tmp/.X*-lock
    rm -rf /tmp/.X11-unix
    rm -f /run/dbus/pid
    log "Desktop stack stopped"
}
trap cleanup_desktop EXIT INT TERM

# --- Environment ---
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1001}"
export DISPLAY="${DISPLAY:-:20}"
export DISPLAY_SIZEW="${DISPLAY_SIZEW:-1920}"
export DISPLAY_SIZEH="${DISPLAY_SIZEH:-1080}"
export DISPLAY_REFRESH="${DISPLAY_REFRESH:-60}"
export DISPLAY_DPI="${DISPLAY_DPI:-96}"
export DISPLAY_CDEPTH="${DISPLAY_CDEPTH:-24}"
export VGL_DISPLAY="${VGL_DISPLAY:-egl}"
export GSTREAMER_PATH="${GSTREAMER_PATH:-/opt/gstreamer}"
export SELKIES_ENABLE_RESIZE="${SELKIES_ENABLE_RESIZE:-false}"
export SELKIES_ENABLE_BASIC_AUTH="${SELKIES_ENABLE_BASIC_AUTH:-false}"
export __GL_SYNC_TO_VBLANK="${__GL_SYNC_TO_VBLANK:-0}"
export PIPEWIRE_LATENCY="${PIPEWIRE_LATENCY:-128/48000}"
export PIPEWIRE_RUNTIME_DIR="${PIPEWIRE_RUNTIME_DIR:-/run/user/1001}"
export PULSE_SERVER="${PULSE_SERVER:-unix:/run/user/1001/pulse/native}"
export PULSE_RUNTIME_PATH="${PULSE_RUNTIME_PATH:-/run/user/1001/pulse}"

log "Desktop environment:"
log "  Display: ${DISPLAY} (${DISPLAY_SIZEW}x${DISPLAY_SIZEH}@${DISPLAY_REFRESH}Hz)"
log "  GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'not detected')"

mkdir -p "${XDG_RUNTIME_DIR}/dbus"
chown -R user:root "${XDG_RUNTIME_DIR}"
rm -rf /tmp/.X* /home/user/.cache

# Ensure log files are writable by user (desktop processes run as uid 1001)
touch /var/log/portal/desktop.log /var/log/desktop.log 2>/dev/null
chmod 666 /var/log/portal/desktop.log /var/log/desktop.log 2>/dev/null

# Wait for provisioning to complete
while [ -f "/.provisioning" ]; do
    log "Startup paused until provisioning completes"
    sleep 10
done

# Install NVIDIA display drivers on first desktop start (deferred from boot)
if [[ -x /opt/supervisor-scripts/nvidia-display-drivers.sh ]]; then
    log "Installing NVIDIA display drivers (first start only)..."
    /opt/supervisor-scripts/nvidia-display-drivers.sh 2>&1 | sed -u 's/^/[nvidia-drivers] /'
    log "NVIDIA display drivers ready"
fi

# --- 1. D-Bus system ---
rm -f /run/dbus/pid
run_bg "dbus-system" dbus-daemon --system --nofork
wait_socket "/run/dbus/system_bus_socket" "D-Bus system"

# --- 2. D-Bus session ---
run_bg_user "dbus-session" dbus-daemon --config-file=/etc/dbus-1/container-session.conf --nofork
wait_socket "${XDG_RUNTIME_DIR}/dbus/session_bus_socket" "D-Bus session"

# --- 3. X server (Xvfb) ---
# Create X11 socket dir as root (user can't create in sticky /tmp)
mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
# Force Mesa software EGL for Xvfb only — NVIDIA's EGL tries GBM which
# needs a real DRM device and segfaults in a virtual framebuffer context.
# Other processes (VirtualGL, Blender, etc.) must use NVIDIA EGL for GPU accel.
run_bg_user "x-server" env __EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/50_mesa.json Xvfb "${DISPLAY}" \
    -ac -screen 0 "8192x4096x${DISPLAY_CDEPTH}" \
    -dpi "${DISPLAY_DPI}" +extension "GLX" +extension "RANDR" +extension "MIT-SHM" \
    +iglx +render -nolisten "tcp" -noreset -shmem
wait_file "/tmp/.X11-unix/X${DISPLAY#*:}" "X server"
sleep 1

# --- 4. VirtualGL desktop patcher ---
if [[ "${DISABLE_VGL,,}" != "true" ]] && nvidia-smi --query-gpu=uuid --format=csv,noheader 2>/dev/null | head -n1 | grep -q .; then
    log "Starting VirtualGL desktop patcher..."
    runuser -u user -- /opt/supervisor-scripts/vgl-desktop-patcher.sh 2>&1 | sed -u 's/^/[vgl] /' &
else
    log "VirtualGL skipped (no GPU or DISABLE_VGL=true)"
fi

# --- 5. PipeWire audio ---
run_bg_user "pipewire" pipewire
wait_socket "${XDG_RUNTIME_DIR}/pipewire-0" "PipeWire"

run_bg_user "pipewire-pulse" pipewire-pulse
run_bg_user "wireplumber" wireplumber
log "Audio stack ready (PipeWire + PulseAudio)"

# --- 6. KDE Plasma ---
export DESKTOP_SESSION="${DESKTOP_SESSION:-plasma}"
export XDG_SESSION_DESKTOP="${XDG_SESSION_DESKTOP:-KDE}"
export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-KDE}"
export XDG_SESSION_TYPE="${XDG_SESSION_TYPE:-x11}"
export XDG_SESSION_ID="${DISPLAY#*:}"
export KDE_FULL_SESSION="${KDE_FULL_SESSION:-true}"
export KDE_SESSION_VERSION="${KDE_SESSION_VERSION:-5}"
export KWIN_COMPOSE="${KWIN_COMPOSE:-N}"
export KWIN_X11_NO_SYNC_TO_VBLANK="${KWIN_X11_NO_SYNC_TO_VBLANK:-1}"
export QT_LOGGING_RULES="${QT_LOGGING_RULES:-*.debug=false;qt.qpa.*=false}"
export GTK_IM_MODULE="${GTK_IM_MODULE:-fcitx}"
export QT_IM_MODULE="${QT_IM_MODULE:-fcitx}"
export SHELL="${SHELL:-/bin/bash}"
run_bg_user "kde" /usr/bin/startplasma-x11

# --- 7. VNC ---
VNC_PASS="${VNC_PASSWORD:-$OPEN_BUTTON_TOKEN}"
runuser -u user -- /usr/bin/x11vnc --storepasswd "${VNC_PASS}" "${XDG_RUNTIME_DIR}/.vncpasswd" 2>/dev/null
run_bg_user "x11vnc" /usr/bin/x11vnc \
    -display "${DISPLAY}" -forever -shared \
    -rfbport 5900 -rfbauth "${XDG_RUNTIME_DIR}/.vncpasswd"
log "VNC server listening on :5900"

# --- 8. TURN server (for Selkies WebRTC NAT traversal) ---
export TURN_HOST="${TURN_HOST:-${PUBLIC_IPADDR:-localhost}}"
export TURN_PORT="${TURN_PORT:-${VAST_TCP_PORT_73478:-73478}}"
export TURN_USERNAME="${TURN_USERNAME:-turnuser}"
export TURN_PASSWORD="${TURN_PASSWORD:-${OPEN_BUTTON_TOKEN:-password}}"

if [[ -n "${VAST_UDP_PORT_73478:-}" ]]; then
    export TURN_PROTOCOL="${TURN_PROTOCOL:-udp}"
else
    export TURN_PROTOCOL="${TURN_PROTOCOL:-tcp}"
fi

if [[ -z "${TURN_SERVER:-}" ]]; then
    log "Starting TURN server (${TURN_PROTOCOL}://${TURN_HOST}:${TURN_PORT})"
    run_bg "coturn" turnserver -n -a \
        --log-file=stdout --lt-cred-mech --fingerprint \
        --no-stun --no-multicast-peers --no-cli --no-tlsv1 --no-tlsv1_1 \
        --realm="vast.ai" \
        --user="${TURN_USERNAME}:${TURN_PASSWORD}" \
        -p "${VAST_UDP_PORT_73478:-${VAST_TCP_PORT_73478:-73478}}" \
        -X "${PUBLIC_IPADDR:-localhost}"
else
    log "Using external TURN server: ${TURN_SERVER}"
fi

# --- 9. Selkies GStreamer (low-latency WebRTC streaming) ---
. /opt/gstreamer/gst-env 2>/dev/null || true
rm -rf "${HOME}/.cache/gstreamer-1.0"
log "Starting Selkies streaming (encoder: ${SELKIES_ENCODER:-x264enc}, TURN: ${TURN_PROTOCOL}://${TURN_HOST}:${TURN_PORT})"
run_bg_user "selkies" selkies-gstreamer \
    --addr="127.0.0.1" \
    --port="16100" \
    --enable_https=false \
    --encoder="${SELKIES_ENCODER:-x264enc}" \
    --enable_basic_auth=false \
    --enable_resize=false \
    --turn_host="${TURN_HOST}" \
    --turn_port="${TURN_PORT}" \
    --turn_protocol="${TURN_PROTOCOL}" \
    --turn_username="${TURN_USERNAME}" \
    --turn_password="${TURN_PASSWORD}"

# --- Persistent display resize ---
# Selkies resets the display resolution when its pipeline (re)initializes.
# This background loop monitors and re-applies the target resolution.
(
    TARGET="${DISPLAY_SIZEW}x${DISPLAY_SIZEH}"
    while true; do
        CURRENT=$(DISPLAY=${DISPLAY} runuser -u user -- xrandr 2>/dev/null | grep '\*' | awk '{print $1}')
        if [[ "$CURRENT" != "$TARGET" ]]; then
            DISPLAY=${DISPLAY} runuser -u user -- /usr/local/bin/selkies-gstreamer-resize "$TARGET" >/dev/null 2>&1 \
                && echo "[desktop] Display resized to $TARGET"
        fi
        sleep 5
    done
) &

# --- All services started ---
log "=========================================="
log "Desktop stack ready"
log "  Selkies (WebRTC): http://localhost:16100"
log "  VNC:              vnc://localhost:5900"
log "  TURN:             ${TURN_PROTOCOL}://${TURN_HOST}:${TURN_PORT}"
log "  Resolution:       ${DISPLAY_SIZEW}x${DISPLAY_SIZEH}"
log "=========================================="

# Sleep forever — supervisor sends TERM to stop, cleanup trap handles teardown.
sleep infinity &
wait $!
