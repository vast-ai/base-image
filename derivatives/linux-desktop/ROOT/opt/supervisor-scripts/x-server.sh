#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

# Check if nvidia display drivers are present - Download if not
if which nvidia-smi > /dev/null; then
    if [ -z "$(ldconfig -N -v $(sed 's/:/ /g' <<< $LD_LIBRARY_PATH) 2>/dev/null | grep 'libEGL_nvidia.so.0')" ] || [ -z "$(ldconfig -N -v $(sed 's/:/ /g' <<< $LD_LIBRARY_PATH) 2>/dev/null | grep 'libGLX_nvidia.so.0')" ]; then
        # Driver version is provided by the kernel through the container toolkit
        export DRIVER_ARCH="$(dpkg --print-architecture | sed -e 's/arm64/aarch64/'  -e 's/i.*86/x86/' -e 's/amd64/x86_64/' -e 's/unknown/x86_64/')"
        export DRIVER_VERSION="$(head -n1 </proc/driver/nvidia/version | awk '{for(i=1;i<=NF;i++) if ($i ~ /^[0-9]+\.[0-9\.]+/) {print $i; exit}}')"
        # Download the correct nvidia driver (check multiple locations)
        echo "Attempt to download driver bundle ${DRIVER_VERSION}/NVIDIA-Linux-${DRIVER_ARCH}-${DRIVER_VERSION}.run"
        cd /tmp
        curl -fsSL -O "https://international.download.nvidia.com/XFree86/Linux-${DRIVER_ARCH}/${DRIVER_VERSION}/NVIDIA-Linux-${DRIVER_ARCH}-${DRIVER_VERSION}.run" || curl -fsSL -O "https://international.download.nvidia.com/tesla/${DRIVER_VERSION}/NVIDIA-Linux-${DRIVER_ARCH}-${DRIVER_VERSION}.run" || { echo "Failed NVIDIA GPU driver download."; }

        if [ -f "/tmp/NVIDIA-Linux-${DRIVER_ARCH}-${DRIVER_VERSION}.run" ]; then
            # Extract installer before installing
            sudo sh "NVIDIA-Linux-${DRIVER_ARCH}-${DRIVER_VERSION}.run" -x
            cd "NVIDIA-Linux-${DRIVER_ARCH}-${DRIVER_VERSION}"
            # Run installation without the kernel modules and host components
            sudo ./nvidia-installer --silent \
                            --no-kernel-module \
                            --install-compat32-libs \
                            --no-nouveau-check \
                            --no-nvidia-modprobe \
                            --no-rpms \
                            --no-backup \
                            --no-check-for-alternate-installs
            sudo rm -rf /tmp/NVIDIA* && cd ~
        fi
    fi
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
