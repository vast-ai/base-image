#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"

# Ensure NVIDIA display drivers are available
if [ -z "$(ldconfig -N -v $(sed 's/:/ /g' <<< $LD_LIBRARY_PATH) 2>/dev/null | grep 'libEGL_nvidia.so.0')" ] || [ -z "$(ldconfig -N -v $(sed 's/:/ /g' <<< $LD_LIBRARY_PATH) 2>/dev/null | grep 'libGLX_nvidia.so.0')" ]; then
    # Driver version is provided by the kernel through the container toolkit
    export DRIVER_ARCH="$(dpkg --print-architecture | sed -e 's/arm64/aarch64/'  -e 's/i.*86/x86/' -e 's/amd64/x86_64/' -e 's/unknown/x86_64/')"
    export DRIVER_VERSION="$(head -n1 </proc/driver/nvidia/version | awk '{ for(i=1;i<=NF;i++) if($i ~ /^[0-9]+\.[0-9]+(\.[0-9]+)?$/) { print $i; exit } }')"
    # Download the correct nvidia driver (check multiple locations)
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


ice="--turn ${TURN_SERVER:-${PUBLIC_IPADDR:-localhost}:${VAST_UDP_PORT_70000:-3478}} --turn-user ${TURN_USER:-user} --turn-pass ${TURN_PASSWORD:-${OPEN_BUTTON_TOKEN:-password}} --stun stun.l.google.com:19302"

/opt/PixelStreamingInfrastructure/SignallingWebServer/platform_scripts/bash/start.sh ${PIXEL_STREAMING_ARGS:-} ${ice}
