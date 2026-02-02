ARG VAST_BASE=vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py312
# Must match the Ubuntu version in VAST_BASE so guacd links against the right SONAMEs
ARG UBUNTU_VERSION=24.04

# Build guacd in a separate stage to keep -dev packages out of the final image
FROM ubuntu:${UBUNTU_VERSION} AS guacamole_builder
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential ca-certificates wget \
    libcairo2-dev libjpeg-turbo8-dev libpng-dev \
    libtool-bin uuid-dev libavcodec-dev libavformat-dev libavutil-dev \
    libswscale-dev freerdp2-dev libpango1.0-dev libvncserver-dev \
    libpulse-dev libssl-dev libvorbis-dev libwebp-dev && \
    cd /tmp && \
    wget https://downloads.apache.org/guacamole/1.5.5/source/guacamole-server-1.5.5.tar.gz && \
    tar -xvf guacamole-server-1.5.5.tar.gz && \
    cd guacamole-server-1.5.5 && \
    ./configure && make && make install DESTDIR=/guacamole-install

FROM ${VAST_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Linux desktop container image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
# NOTE: ROOT/opt/instance-tools/export_env.sh overrides the base image version.
# Remove once the base image fix is released.
COPY ./ROOT /

# Install a basic GPU accelerated graphical environment
ARG TARGETARCH
RUN \
    set -euo pipefail && \
    add-apt-repository ppa:pipewire-debian/pipewire-upstream -y && \
    add-apt-repository ppa:pipewire-debian/wireplumber-upstream -y && \
    apt-get update && \
    # Many base packages (build-essential, ca-certificates, curl, git, wget, etc.)
    # are already in the base image -- only list packages this layer adds.
    BASE_PACKAGES=( \
        alsa-base \
        alsa-utils \
        aom-tools \
        bzip2 \
        coturn \
        dbus \
        file \
        fonts-hack \
        fonts-liberation \
        fonts-noto \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        fonts-noto-color-emoji \
        fonts-noto-extra \
        fonts-noto-hinted \
        fonts-noto-mono \
        fonts-noto-ui-extra \
        fonts-noto-unhinted \
        fonts-opensymbol \
        fonts-symbola \
        glib-networking \
        gnupg \
        gstreamer1.0-plugins-bad \
        gzip \
        kmod \
        lame \
        libavcodec-extra \
        libdrm2 \
        libegl1 \
        libgcrypt20 \
        libgirepository-1.0-1 \
        libgl1 \
        libgles1 \
        libgles2 \
        libglib2.0-0 \
        libglu1 \
        libglvnd0 \
        libglx0 \
        libgudev-1.0-0 \
        libopengl0 \
        libopus0 \
        libpci3 \
        libpulse0 \
        libsm6 \
        libspa-0.2-bluetooth \
        libspa-0.2-jack \
        libva2 \
        libvdpau-va-gl1 \
        libvdpau1 \
        libwayland-egl1 \
        libx11-6 \
        libx11-xcb1 \
        libxau6 \
        libxcb-dri3-0 \
        libxcb1 \
        libxdamage1 \
        libxdmcp6 \
        libxext6 \
        libxfixes3 \
        libxtst6 \
        libxv1 \
        novnc \
        pipewire \
        pipewire-audio-client-libraries \
        pipewire-pulse \
        python3-gi \
        python3-numpy \
        python3-setuptools \
        python3-wheel \
        rar \
        svt-av1 \
        unar \
        unrar \
        vainfo \
        vdpauinfo \
        websockify \
        wireplumber \
        wmctrl \
        x11-apps \
        x11-utils \
        x11-xkb-utils \
        x11-xserver-utils \
        x11vnc \
        x264 \
        x265 \
        xauth \
        xbitmaps \
        xcvt \
        xdg-user-dirs \
        xdg-utils \
        xdotool \
        xfonts-base \
        xfonts-scalable \
        xinit \
        xkb-data \
        xsel \
        xserver-xorg-core \
        xserver-xorg-input-all \
        xserver-xorg-input-wacom \
        xserver-xorg-video-fbdev \
        xsettingsd \
        xvfb \
    ) && \
    I386_PACKAGES=( \
        libc6:i386 \
        libdrm2:i386 \
        libegl1:i386 \
        libgl1:i386 \
        libgles1:i386 \
        libgles2:i386 \
        libglu1:i386 \
        libglvnd0:i386 \
        libglx0:i386 \
        libopengl0:i386 \
        libsm6:i386 \
        libva2:i386 \
        libvulkan-dev:i386 \
        libx11-6:i386 \
        libxau6:i386 \
        libxcb1:i386 \
        libxdmcp6:i386 \
        libxext6:i386 \
        libxtst6:i386 \
        libxv1:i386 \
    ) && \
    if [[ $TARGETARCH == "amd64" ]]; then \
        apt-get install --no-install-recommends -y "${BASE_PACKAGES[@]}" "${I386_PACKAGES[@]}"; \
    else \
        apt-get install --no-install-recommends -y "${BASE_PACKAGES[@]}"; \
    fi && \
    ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html

# Vulkan & EGL fallback configs for NVIDIA.
# The NVIDIA Container Toolkit bind-mounts its own versions of these files at
# runtime, shadowing the baked-in copies below.  These only take effect on
# machines where the toolkit is absent or misconfigured.
RUN \
    set -euo pipefail && \
    VULKAN_API_VERSION=$(dpkg -s libvulkan1 | grep -oP 'Version: [0-9|\.]+' | grep -oP '[0-9]+(\.[0-9]+)(\.[0-9]+)') && \
    mkdir -pm755 /etc/vulkan/icd.d/ && \
    printf '{"file_format_version" : "1.0.0","ICD": {"library_path": "libGLX_nvidia.so.0","api_version" : "%s"}}' "$VULKAN_API_VERSION" > /etc/vulkan/icd.d/nvidia_icd.json && \
    mkdir -pm755 /usr/share/glvnd/egl_vendor.d/ && \
    printf '{"file_format_version" : "1.0.0","ICD": {"library_path": "libEGL_nvidia.so.0"}}' > /usr/share/glvnd/egl_vendor.d/10_nvidia.json

# Install Virtual GL
RUN \
    set -euo pipefail && \
    VIRTUALGL_VERSION="$(curl -fsSL "https://api.github.com/repos/VirtualGL/virtualgl/releases/latest" | jq -r '.tag_name' | sed 's/[^0-9\.\-]*//g')" && \
    cd /tmp && \
    curl -fsSL -O "https://github.com/VirtualGL/virtualgl/releases/download/${VIRTUALGL_VERSION}/virtualgl_${VIRTUALGL_VERSION}_${TARGETARCH}.deb" && \
    apt-get install -y ./virtualgl_${VIRTUALGL_VERSION}_${TARGETARCH}.deb && \
    chmod -f u+s /usr/lib/libvglfaker.so /usr/lib/libvglfaker-nodl.so /usr/lib/libdlfaker.so /usr/lib/libgefaker.so && \
    if [[ $TARGETARCH == "amd64" ]]; then \
        curl -fsSL -O "https://github.com/VirtualGL/virtualgl/releases/download/${VIRTUALGL_VERSION}/virtualgl32_${VIRTUALGL_VERSION}_${TARGETARCH}.deb" && \
        apt-get install -y ./virtualgl32_${VIRTUALGL_VERSION}_${TARGETARCH}.deb && \
        chmod -f u+s /usr/lib32/libvglfaker.so /usr/lib32/libvglfaker-nodl.so /usr/lib32/libvglfaker-opencl.so /usr/lib32/libdlfaker.so /usr/lib32/libgefaker.so && \
        chmod -f u+s /usr/lib/i386-linux-gnu/libvglfaker.so /usr/lib/i386-linux-gnu/libvglfaker-nodl.so /usr/lib/i386-linux-gnu/libvglfaker-opencl.so /usr/lib/i386-linux-gnu/libdlfaker.so /usr/lib/i386-linux-gnu/libgefaker.so; \
    fi && \
    rm -f /tmp/virtualgl*

# NVIDIA VAAPI (install build-only deps here, cleaned up after Selkies)
RUN \
    set -euo pipefail && \
    apt-get update && apt-get install --no-install-recommends -y \
        meson pkg-config libc6-dev libegl-dev libglvnd-dev libva-dev \
        libopenh264-dev libvpx-dev libvulkan-dev libwayland-dev \
        libxrandr-dev libgstreamer-plugins-bad1.0-dev libffmpeg-nvenc-dev \
        wayland-protocols libelf-dev && \
    NVIDIA_VAAPI_DRIVER_VERSION="$(curl -fsSL "https://api.github.com/repos/elFarto/nvidia-vaapi-driver/releases/latest" | jq -r '.tag_name' | sed 's/[^0-9\.\-]*//g')" && \
    cd /tmp && \
    curl -fsSL "https://github.com/elFarto/nvidia-vaapi-driver/archive/v${NVIDIA_VAAPI_DRIVER_VERSION}.tar.gz" | tar -xzf - && \
    mv -f nvidia-vaapi-driver* nvidia-vaapi-driver && \
    cd nvidia-vaapi-driver && \
    meson setup build && \
    meson install -C build && \
    rm -rf /tmp/* && \
    echo "/usr/local/nvidia/lib" >> /etc/ld.so.conf.d/nvidia.conf && \
    echo "/usr/local/nvidia/lib64" >> /etc/ld.so.conf.d/nvidia.conf

# Install Selkies
# AMD64 only until fix
RUN \
    set -euo pipefail && \
    cd /opt && \
    SELKIES_VERSION="$(curl -fsSL "https://api.github.com/repos/selkies-project/selkies-gstreamer/releases/latest" | jq -r '.tag_name' | sed 's/[^0-9\.\-]*//g')" && \
    cd /opt && curl -fsSL "https://github.com/selkies-project/selkies-gstreamer/releases/download/v${SELKIES_VERSION}/gstreamer-selkies_gpl_v${SELKIES_VERSION}_ubuntu$(grep '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '\"')_$(dpkg --print-architecture).tar.gz" | tar -xzf - && \
    cd /tmp && curl -O -fsSL "https://github.com/selkies-project/selkies-gstreamer/releases/download/v${SELKIES_VERSION}/selkies_gstreamer-${SELKIES_VERSION}-py3-none-any.whl" && /usr/bin/pip3 install --no-cache-dir --force-reinstall "selkies_gstreamer-${SELKIES_VERSION}-py3-none-any.whl" "websockets<14.0" && rm -f "selkies_gstreamer-${SELKIES_VERSION}-py3-none-any.whl" && \
    cd /opt && curl -fsSL "https://github.com/selkies-project/selkies-gstreamer/releases/download/v${SELKIES_VERSION}/selkies-gstreamer-web_v${SELKIES_VERSION}.tar.gz" | tar -xzf - && \
    cd /tmp && curl -o selkies-js-interposer.deb -fsSL "https://github.com/selkies-project/selkies-gstreamer/releases/download/v${SELKIES_VERSION}/selkies-js-interposer_v${SELKIES_VERSION}_ubuntu$(grep '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '\"')_$(dpkg --print-architecture).deb" && apt-get update && apt-get install --no-install-recommends -y ./selkies-js-interposer.deb && rm -f selkies-js-interposer.deb

# Clean up build-only packages installed for NVIDIA VAAPI
RUN apt-get purge -y --auto-remove \
    meson libegl-dev libglvnd-dev libva-dev libopenh264-dev \
    libvpx-dev libvulkan-dev libwayland-dev libxrandr-dev \
    libgstreamer-plugins-bad1.0-dev libffmpeg-nvenc-dev \
    wayland-protocols libelf-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

ENV CATALINA_BASE=/opt/tomcat9
ENV CATALINA_HOME=/opt/tomcat9

# Apache Tomcat
RUN \
    set -euo pipefail && \
    TOMCAT_VERSION="$(curl -fsSL https://dlcdn.apache.org/tomcat/tomcat-9/ | grep -oP 'v\K[0-9]+\.[0-9]+\.[0-9]+' | sort -V | tail -n1)" && \
    cd /opt && \
    wget "https://dlcdn.apache.org/tomcat/tomcat-9/v${TOMCAT_VERSION}/bin/apache-tomcat-${TOMCAT_VERSION}.tar.gz" && \
    tar xzf "apache-tomcat-${TOMCAT_VERSION}.tar.gz" && \
    rm "apache-tomcat-${TOMCAT_VERSION}.tar.gz" && \
    ln -s "/opt/apache-tomcat-${TOMCAT_VERSION}" /opt/tomcat9 && \
    chmod +x /opt/tomcat9/bin/*.sh && \
    useradd -r -m -U -d /opt/tomcat9 -s /bin/false tomcat && \
    chown -R tomcat:tomcat "/opt/apache-tomcat-${TOMCAT_VERSION}" && \
    chmod -R 755 "/opt/apache-tomcat-${TOMCAT_VERSION}"

# Guacamole - install pre-built guacd from builder stage + runtime libraries
COPY --from=guacamole_builder /guacamole-install/ /
RUN \
    set -euo pipefail && \
    ldconfig && \
    apt-get update && \
    # Stable package names (same across Ubuntu 22.04 / 24.04)
    apt-get install --no-install-recommends -y \
        libcairo2 libjpeg-turbo8 libuuid1 \
        libpango-1.0-0 libvncserver1 \
        libpulse0 libvorbis0a libwebp7 && \
    # Version-specific packages: ffmpeg SONAMEs and t64 ABI transition
    . /etc/os-release && \
    case "$VERSION_ID" in \
        24.04) apt-get install --no-install-recommends -y \
            libavcodec60 libavformat60 libavutil58 libswscale7 \
            libpng16-16t64 libfreerdp-client2-2t64 libssl3t64 ;; \
        *) apt-get install --no-install-recommends -y \
            libavcodec58 libavformat58 libavutil56 libswscale5 \
            libpng16-16 libfreerdp-client2-2 libssl3 ;; \
    esac && \
    sed -i 's/port="8080"/port="16200" address="127.0.0.1"/' /opt/tomcat9/conf/server.xml && \
    wget -O /opt/tomcat9/webapps/guacamole.war \
        https://downloads.apache.org/guacamole/1.5.5/binary/guacamole-1.5.5.war && \
    chown tomcat:tomcat /opt/tomcat9/webapps/guacamole.war && \
    mkdir -p /etc/guacamole/{extensions,lib} && \
    wget -O /etc/guacamole/extensions/guacamole-auth-noauth-1.5.5.jar \
        https://github.com/GauriSpears/guacamole-noauth/releases/download/1.5.5/guacamole-auth-noauth-1.5.5.jar && \
    echo "guacd-hostname: localhost" > /etc/guacamole/guacamole.properties && \
    echo "guacd-port: 4822" >> /etc/guacamole/guacamole.properties && \
    echo "noauth-config: /etc/guacamole/noauth-config.xml" >> /etc/guacamole/guacamole.properties && \
    touch /etc/guacamole/noauth-config.xml && \
    chown -R tomcat:tomcat /etc/guacamole && \
    chmod -R 755 /etc/guacamole && \
    chown tomcat:tomcat /var/log/portal

# Install DE
RUN \
    set -euo pipefail && \
    apt-get update && apt-get install --install-recommends -y \
    kde-baseapps \
    plasma-desktop \
    plasma-workspace \
    adwaita-icon-theme-full \
    appmenu-gtk3-module \
    ark \
    aspell \
    aspell-en \
    breeze \
    breeze-cursor-theme \
    breeze-gtk-theme \
    breeze-icon-theme \
    dbus-x11 \
    debconf-kde-helper \
    desktop-file-utils \
    dolphin \
    dolphin-plugins \
    enchant-2 \
    fcitx \
    fcitx-frontend-gtk2 \
    fcitx-frontend-gtk3 \
    fcitx-frontend-qt5 \
    fcitx-module-dbus \
    fcitx-module-kimpanel \
    fcitx-module-lua \
    fcitx-module-x11 \
    fcitx-tools \
    fcitx-hangul \
    fcitx-libpinyin \
    fcitx-m17n \
    fcitx-mozc \
    fcitx-sayura \
    fcitx-unikey \
    filelight \
    frameworkintegration \
    gwenview \
    hunspell \
    im-config \
    kwrite \
    kcalc \
    kcharselect \
    kdeadmin \
    kde-config-fcitx \
    kde-config-gtk-style \
    kde-config-gtk-style-preview \
    kdeconnect \
    kdegraphics-thumbnailers \
    kde-spectacle \
    kdf \
    kdialog \
    kfind \
    kget \
    khotkeys \
    kimageformat-plugins \
    kinfocenter \
    kio \
    kio-extras \
    kmag \
    kmenuedit \
    kmix \
    kmousetool \
    kmouth \
    ksshaskpass \
    ktimer \
    kwin-addons \
    kwin-x11 \
    libdbusmenu-glib4 \
    libdbusmenu-gtk3-4 \
    libgail-common \
    libgdk-pixbuf2.0-bin \
    libgtk2.0-bin \
    libgtk-3-bin \
    libkf5baloowidgets-bin \
    libkf5dbusaddons-bin \
    libkf5iconthemes-bin \
    libkf5kdelibs4support5-bin \
    libkf5khtml-bin \
    libkf5parts-plugins \
    libpam0g \
    libpam-modules \
    libqt5multimedia5-plugins \
    libreoffice \
    librsvg2-common \
    media-player-info \
    okular \
    okular-extra-backends \
    plasma-browser-integration \
    plasma-calendar-addons \
    plasma-dataengines-addons \
    plasma-integration \
    plasma-runners-addons \
    plasma-widgets-addons \
    qml-module-org-kde-runnermodel \
    qml-module-org-kde-qqc2desktopstyle \
    qml-module-qtgraphicaleffects \
    qml-module-qt-labs-platform \
    qml-module-qtquick-xmllistmodel \
    qt5-gtk-platformtheme \
    qt5-image-formats-plugins \
    qt5-style-plugins \
    qtspeech5-flite-plugin \
    qtvirtualkeyboard-plugin \
    software-properties-qt \
    sonnet-plugins \
    sweeper \
    systemsettings \
    ubuntu-drivers-common \
    vlc \
    vlc-plugin-access-extra \
    vlc-plugin-notify \
    vlc-plugin-samba \
    vlc-plugin-skins2 \
    vlc-plugin-video-splitter \
    vlc-plugin-visualization

# Google Chrome
RUN \
    cd /tmp && \
    curl -o google-chrome-stable.deb -fsSL "https://dl.google.com/linux/direct/google-chrome-stable_current_$(dpkg --print-architecture).deb" && \
    apt-get install -y ./google-chrome-stable.deb && \
    update-alternatives --install /usr/bin/google-chrome-stable google-chrome-stable /usr/local/bin/google-chrome 100 && \
    update-alternatives --set x-www-browser /usr/bin/google-chrome-stable

# Blender
RUN \
    BLENDER_VERSION=4.4.1 && \
    cd /opt && \
    wget "https://download.blender.org/release/Blender${BLENDER_VERSION%.*}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
    tar xvf "blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
    rm "blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
    ln -s "/opt/blender-${BLENDER_VERSION}-linux-x64/blender" /usr/bin/blender && \
    cp "/opt/blender-${BLENDER_VERSION}-linux-x64/blender.desktop" /usr/share/applications && \
    cp "/opt/blender-${BLENDER_VERSION}-linux-x64/blender.svg" /usr/share/icons/hicolor/scalable/apps/


# GPU benchmarks
RUN \
    set -euo pipefail && \
    apt-get update && apt-get install --no-install-recommends -y glmark2 && \
    cd /tmp && \
    wget -O Unigine_Heaven-4.0.run https://assets.unigine.com/d/Unigine_Heaven-4.0.run && \
    chmod +x Unigine_Heaven-4.0.run && \
    ./Unigine_Heaven-4.0.run --target /opt/unigine-heaven --noexec && \
    ln -s /opt/unigine-heaven/heaven /usr/local/bin/heaven && \
    rm -f /tmp/Unigine_Heaven-4.0.run

# Switch to user for simplicity.  We will switch back to root as all Vast.ai instances default to root login anyway
ENV XDG_RUNTIME_DIR="/run/user/1001"
ENV DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1001/dbus/session_bus_socket"
USER user
RUN mkdir -p "$XDG_RUNTIME_DIR/dbus"

ENV DISPLAY=":20"
ENV PIPEWIRE_LATENCY="128/48000"
ENV PIPEWIRE_RUNTIME_DIR="/run/user/1001"
ENV PULSE_SERVER=unix:"/run/user/1001/pulse/native"
ENV PULSE_RUNTIME_PATH="/run/user/1001/pulse"

# Selkies config
ENV __GL_SYNC_TO_VBLANK=0
ENV DISPLAY_SIZEW=1920
ENV DISPLAY_SIZEH=1080
ENV DISPLAY_REFRESH=60
ENV DISPLAY_DPI=96
ENV DISPLAY_CDEPTH=24
ENV VGL_DISPLAY=egl
ENV SELKIES_ENABLE_RESIZE=false
ENV SELKIES_ENABLE_BASIC_AUTH=false
ENV GSTREAMER_PATH=/opt/gstreamer

# DE Config
ENV DESKTOP_SESSION=plasma
ENV XDG_SESSION_DESKTOP=KDE
ENV XDG_CURRENT_DESKTOP=KDE
ENV XDG_SESSION_TYPE=x11
ENV KDE_FULL_SESSION=true
ENV KDE_SESSION_VERSION=5
ENV KDE_APPLICATIONS_AS_SCOPE=1
ENV KWIN_COMPOSE=N
ENV KWIN_EFFECTS_FORCE_ANIMATIONS=0
ENV KWIN_EXPLICIT_SYNC=0
ENV KWIN_X11_NO_SYNC_TO_VBLANK=1

# Input
ENV GTK_IM_MODULE=fcitx
ENV QT_IM_MODULE=fcitx
ENV XIM=fcitx
ENV XMODIFIERS="@im=fcitx"

USER root




