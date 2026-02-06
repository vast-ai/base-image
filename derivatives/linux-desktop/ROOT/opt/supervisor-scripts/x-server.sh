#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

# Log GPU driver status for debugging
if which nvidia-smi > /dev/null 2>&1; then
    if [ -z "$(ldconfig -N -v $(sed 's/:/ /g' <<< $LD_LIBRARY_PATH) 2>/dev/null | grep 'libGLX_nvidia.so.0')" ]; then
        echo "NVIDIA GPU detected but display drivers not present. Using Mesa/llvmpipe for rendering."
    fi
else
    echo "No NVIDIA GPU detected. Using Mesa/llvmpipe for rendering."
fi

sudo rm -rf /tmp/.X*
rm -rf /home/user/.cache

socket="$XDG_RUNTIME_DIR/dbus/session_bus_socket"
echo "Waiting for ${socket}..."
while [[ ! -S $socket ]]; do
    sleep 1
done

function delayedResize() {
    sleep 15
    /usr/local/bin/selkies-gstreamer-resize "${DISPLAY_SIZEW}x${DISPLAY_SIZEH}"
}

echo "Starting Xvfb..."

delayedResize &

# Force Mesa software EGL for Xvfb -- NVIDIA's EGL tries GBM which needs a
# real DRM device and segfaults in a virtual framebuffer context.
export __EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/50_mesa.json
/usr/bin/Xvfb "${DISPLAY}" -screen 0 "8192x4096x${DISPLAY_CDEPTH}" -dpi "${DISPLAY_DPI}" \
    +extension "COMPOSITE" +extension "DAMAGE" +extension "GLX" +extension "RANDR" \
    +extension "RENDER" +extension "MIT-SHM" +extension "XFIXES" +extension "XTEST" \
    +iglx +render -nolisten "tcp" -ac -noreset -shmem
