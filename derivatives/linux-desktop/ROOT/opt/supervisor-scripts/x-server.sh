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

# Apply the requested WIDTHxHEIGHT to the Xvfb screen with xrandr. Used as a
# fallback where Selkies' own selkies-gstreamer-resize is unavailable: Selkies
# ships amd64-only release artifacts, so on other arches (arm64/sbsa) that
# helper is absent and the desktop would otherwise stay at Xvfb's full
# 8192x4096 startup framebuffer.
function resizeWithXrandr() {
    local res="$1"
    local w="${res%x*}" h="${res#*x}" output mode line
    output="$(xrandr --query | awk '/ connected/{print $1; exit}')"
    output="${output:-screen}"
    mode="${w}x${h}"
    if ! xrandr --query | grep -qw "${mode}"; then
        line="$(cvt "${w}" "${h}" 60 | sed -n 's/^Modeline //p' | cut -d' ' -f2-)"
        xrandr --newmode "${mode}" ${line}
        xrandr --addmode "${output}" "${mode}"
    fi
    xrandr --output "${output}" --mode "${mode}"
}

function delayedResize() {
    sleep 15
    local target="${DISPLAY_SIZEW}x${DISPLAY_SIZEH}"
    if [[ -x /usr/local/bin/selkies-gstreamer-resize ]]; then
        /usr/local/bin/selkies-gstreamer-resize "${target}"
    else
        resizeWithXrandr "${target}"
    fi
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
