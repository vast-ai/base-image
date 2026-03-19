ARG VAST_BASE=vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04

FROM ${VAST_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Unreal Pixel Streaming image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Install a basic GPU accelerated graphical environment
RUN \
    set -euo pipefail && \
    add-apt-repository ppa:pipewire-debian/pipewire-upstream -y && \
    apt-get update && \
    apt-get install --no-install-recommends -y \
        kmod \
        dbus \
        coturn \
        vulkan-tools \
        mesa-vulkan-drivers:i386 \
        libvulkan-dev:i386 \
        libc6:i386 \
        libxau6:i386 \
        libxdmcp6:i386 \
        libxcb1:i386 \
        libxext6:i386 \
        libx11-6:i386 \
        libxv1:i386 \
        libxtst6:i386 \
        libdrm2:i386 \
        libegl1:i386 \
        libgl1:i386 \
        libopengl0:i386 \
        libgles1:i386 \
        libgles2:i386 \
        libglvnd0:i386 \
        libglx0:i386 \
        libglu1:i386 \
        libsm6:i386 \
        libc6-dev \
        libpci3 \
        libelf-dev \
        libglvnd-dev \
        libxau6 \
        libxdmcp6 \
        libxcb1 \
        libxext6 \
        libx11-6 \
        libxv1 \
        libxtst6 \
        libdrm2 \
        libegl1 \
        libgl1 \
        libopengl0 \
        libgles1 \
        libgles2 \
        libglvnd0 \
        libglx0 \
        libglu1 \
        libsm6 \
        intel-gpu-tools \
        mesa-vulkan-drivers:i386 \
        libvulkan-dev:i386 \
        libc6:i386 \
        libxau6:i386 \
        libxdmcp6:i386 \
        libxcb1:i386 \
        libxext6:i386 \
        libx11-6:i386 \
        libxv1:i386 \
        libxtst6:i386 \
        libdrm2:i386 \
        libegl1:i386 \
        libgl1:i386 \
        libopengl0:i386 \
        libgles1:i386 \
        libgles2:i386 \
        libglvnd0:i386 \
        libglx0:i386 \
        libglu1:i386 \
        libsm6:i386 \
        libegl-dev \
        xvfb \
        pipewire \
        pipewire-audio-client-libraries \
        pipewire-pulse wireplumber \
        libspa-0.2-bluetooth \
        libspa-0.2-jack

# Install latest Vulkan version
RUN \
    set -euo pipefail && \
    VULKAN_API_VERSION=$(dpkg -s libvulkan1 | grep -oP 'Version: [0-9|\.]+' | grep -oP '[0-9]+(\.[0-9]+)(\.[0-9]+)') && \
    mkdir -pm755 /etc/vulkan/icd.d/ && \
    printf '{"file_format_version" : "1.0.0","ICD": {"library_path": "libGLX_nvidia.so.0","api_version" : "%s"}}' $VULKAN_API_VERSION > /etc/vulkan/icd.d/nvidia_icd.json

# Switch to user for simplicity.  We will switch back to root as all Vast.ai instances default to root login anyway
USER user
RUN \
    set -euo pipefail && \
    mkdir /run/user/1001/dbus && \
    mkdir -p /home/user/.config/dbus && \
    echo '<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">\
    <busconfig>\
        <type>session</type>\
        <keep_umask/>\
        <listen>unix:path=/run/user/1001/dbus/session_bus_socket</listen>\
        <auth>EXTERNAL</auth>\
        <allow_anonymous/>\
        <policy context="default">\
            <allow send_destination="*" eavesdrop="true"/>\
            <allow eavesdrop="true"/>\
            <allow own="*"/>\
        </policy>\
    </busconfig>' > /home/user/.config/dbus/session-local.conf 

ENV XDG_RUNTIME_DIR=/run/user/1001
ENV DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/dbus/session_bus_socket"
ENV PULSE_SERVER=unix:${XDG_RUNTIME_DIR}/pulse/native

USER root

# Install latest Pixel Streaming Infrastructure
ARG PIXEL_STREAMING_REF

RUN \
    set -euo pipefail && \
    [ -n "${PIXEL_STREAMING_REF}" ] || (echo "Must specify PIXEL_STREAMING_REF" && exit 1) && \
    touch /tmp/install.log && \
    git clone https://github.com/EpicGamesExt/PixelStreamingInfrastructure -b ${PIXEL_STREAMING_REF} /opt/PixelStreamingInfrastructure && \
    jq '.player_port = "13000"' /opt/PixelStreamingInfrastructure/SignallingWebServer/config.json | sponge /opt/PixelStreamingInfrastructure/SignallingWebServer/config.json && \
    /opt/PixelStreamingInfrastructure/SignallingWebServer/platform_scripts/bash/setup.sh && \
    /opt/PixelStreamingInfrastructure/SignallingWebServer/platform_scripts/bash/start.sh > /tmp/install.log 2>&1 & \
    while ! grep -q "Http server listening on port 13000" /tmp/install.log; do sleep 1; done && \
    pkill -f "platform_scripts/bash/start.sh" && \
    rm -f /tmp/install.log

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
