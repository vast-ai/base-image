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
        inotify-tools \
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
        mesa-vulkan-drivers \
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
        pipewire \
        pipewire-audio-client-libraries \
        gstreamer1.0-pipewire \
        pipewire-pulse \
        python3-gi \
        python3-numpy \
        python3-setuptools \
        python3-wheel \
        svt-av1 \
        unar \
        vainfo \
        vdpauinfo \
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
        xclip \
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
    # `rar` and `unrar` are proprietary closed-source archivers (RarLab);
    # Ubuntu noble only publishes them for amd64/i386. Skip on arm64 — the
    # free `unar` (already in BASE_PACKAGES) handles both RAR extraction
    # and a wider set of formats, so arm64 users still have an unarchiver.
    AMD64_ONLY_PACKAGES=( \
        rar \
        unrar \
    ) && \
    if [[ $TARGETARCH == "amd64" ]]; then \
        apt-get install --no-install-recommends -y "${BASE_PACKAGES[@]}" "${I386_PACKAGES[@]}" "${AMD64_ONLY_PACKAGES[@]}"; \
    else \
        apt-get install --no-install-recommends -y "${BASE_PACKAGES[@]}"; \
    fi

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
    # Multiarch directory varies by host CPU: x86_64-linux-gnu on amd64,
    # aarch64-linux-gnu on arm64. Derive it so the libvglfaker.so symlink
    # lands in the right place (the arm64 dir does not even exist on amd64
    # systems and vice versa, so a hardcoded path breaks one of the two).
    case "$(dpkg --print-architecture)" in \
        amd64) MULTIARCH_DIR="x86_64-linux-gnu" ;; \
        arm64) MULTIARCH_DIR="aarch64-linux-gnu" ;; \
        *) echo "Unsupported arch for VirtualGL symlink: $(dpkg --print-architecture)"; exit 1 ;; \
    esac && \
    mkdir -p "/usr/lib/${MULTIARCH_DIR}" && \
    ln -sf /usr/lib/libvglfaker.so "/usr/lib/${MULTIARCH_DIR}/libvglfaker.so" && \
    if [[ $TARGETARCH == "amd64" ]]; then \
        curl -fsSL -O "https://github.com/VirtualGL/virtualgl/releases/download/${VIRTUALGL_VERSION}/virtualgl32_${VIRTUALGL_VERSION}_${TARGETARCH}.deb" && \
        apt-get install -y ./virtualgl32_${VIRTUALGL_VERSION}_${TARGETARCH}.deb && \
        chmod -f u+s /usr/lib32/libvglfaker.so /usr/lib32/libvglfaker-nodl.so /usr/lib32/libvglfaker-opencl.so /usr/lib32/libdlfaker.so /usr/lib32/libgefaker.so && \
        chmod -f u+s /usr/lib/i386-linux-gnu/libvglfaker.so /usr/lib/i386-linux-gnu/libvglfaker-nodl.so /usr/lib/i386-linux-gnu/libvglfaker-opencl.so /usr/lib/i386-linux-gnu/libdlfaker.so /usr/lib/i386-linux-gnu/libgefaker.so; \
    fi && \
    rm -f /tmp/virtualgl*

# Driver libraries mounted by the NVIDIA runtime live here on some hosts.
RUN \
    echo "/usr/local/nvidia/lib" >> /etc/ld.so.conf.d/nvidia.conf && \
    echo "/usr/local/nvidia/lib64" >> /etc/ld.so.conf.d/nvidia.conf

# Selkies 2.0, pinned with a per-arch checksum (ADR 0050): upstream deletes old releases
# and changes behaviour under unchanged flag names, so it is never fetched from
# releases/latest. The .deb carries its own venv at /opt/selkies on the system Python.
ARG SELKIES_VERSION=2.0.0
ARG SELKIES_SHA256_AMD64=bbaa4d71012b9374a753b7dfddc1da07e31f34b04277fe4fb3d045f18fd88391
ARG SELKIES_SHA256_ARM64=3900f3ba805898c495829629092553cc1cf4d5a864ffc4056f57d21646ad45e4
COPY ./selkies/nvreach.c /tmp/nvreach.c
RUN \
    set -euo pipefail && \
    arch="$(dpkg --print-architecture)" && \
    case "$arch" in \
        amd64) sha="${SELKIES_SHA256_AMD64}" ;; \
        arm64) sha="${SELKIES_SHA256_ARM64}" ;; \
        *) echo "FATAL: no Selkies checksum for ${arch}"; exit 1 ;; \
    esac && \
    deb="selkies-${SELKIES_VERSION}-ubuntu$(. /etc/os-release && echo "${VERSION_ID}")-${arch}.deb" && \
    curl -fsSL -o "/tmp/${deb}" "https://github.com/selkies-project/selkies/releases/download/${SELKIES_VERSION}/${deb}" && \
    echo "${sha}  /tmp/${deb}" | sha256sum -c - && \
    apt-get update && \
    apt-get install --no-install-recommends -y "/tmp/${deb}" && \
    rm -f "/tmp/${deb}" && \
    selkies --version 2>&1 | grep -qx "selkies ${SELKIES_VERSION}" && \
    mkdir -p /usr/local/lib/selkies && \
    gcc -shared -fPIC -O2 -Wall -Wextra -Werror -o /usr/local/lib/selkies/nvreach.so /tmp/nvreach.c -ldl && \
    rm -f /tmp/nvreach.c && \
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
    chmod -R 755 /etc/guacamole

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

# Firefox (both arches) via Mozilla's official APT repo. The Ubuntu noble
# `firefox` package is a snap transitional and snap doesn't work in
# containers; Mozilla's own apt repo (packages.mozilla.org/apt) publishes
# real .debs for both amd64 and arm64. The Pin-Priority pin makes apt
# prefer Mozilla's deb over the Ubuntu snap stub.
RUN \
    set -euo pipefail && \
    install -d -m 0755 /etc/apt/keyrings && \
    curl -fsSL https://packages.mozilla.org/apt/repo-signing-key.gpg -o /etc/apt/keyrings/packages.mozilla.org.asc && \
    echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" > /etc/apt/sources.list.d/mozilla.list && \
    printf 'Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n' > /etc/apt/preferences.d/mozilla && \
    apt-get update && \
    apt-get install --no-install-recommends -y firefox && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Google Chrome (amd64 only — Google doesn't publish an aarch64 .deb).
# Installed alongside Firefox; the update-alternatives below makes Chrome
# the default `x-www-browser` on amd64 so existing user-facing behavior
# is preserved. On arm64 the default stays at Firefox (set automatically
# when firefox is the only x-www-browser candidate apt installed).
RUN \
    set -euo pipefail && \
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        cd /tmp && \
        curl -o google-chrome-stable.deb -fsSL "https://dl.google.com/linux/direct/google-chrome-stable_current_$(dpkg --print-architecture).deb" && \
        # The preceding Firefox step ended with `rm -rf /var/lib/apt/lists/*`,
        # so apt has no package lists. Repopulate them before installing the
        # local .deb — Chrome pulls in runtime deps (libnss3, fonts-liberation,
        # etc.) that apt must resolve from the indexes.
        apt-get update && \
        apt-get install -y ./google-chrome-stable.deb && \
        rm -f google-chrome-stable.deb && \
        apt-get clean && rm -rf /var/lib/apt/lists/* && \
        update-alternatives --install /usr/bin/google-chrome-stable google-chrome-stable /usr/local/bin/google-chrome 100 && \
        update-alternatives --set x-www-browser /usr/bin/google-chrome-stable; \
    else \
        echo "Skipping Google Chrome on $(dpkg --print-architecture); Firefox above is the default browser."; \
    fi

# Blender:
#   amd64 -> install from Blender's official tarball at the latest release
#            (auto-detected, overridable via the BLENDER_VERSION build arg).
#            The Blender project only publishes linux-x64 tarballs — no
#            Linux arm64 builds exist in the release archive at any version.
#   arm64 -> install from Ubuntu noble's apt repo (blender 4.0.2+dfsg).
#            Older than the official tarball but a real working package;
#            sidesteps the Ubuntu snap transitional that wouldn't function
#            in a container.
ARG BLENDER_VERSION=""
RUN \
    set -euo pipefail && \
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        if [ -z "$BLENDER_VERSION" ]; then \
            MAJOR_MINOR=$(curl -fsSL "https://download.blender.org/release/" | \
                grep -oP 'Blender\K\d+\.\d+' | sort -V | tail -1) && \
            BLENDER_VERSION=$(curl -fsSL "https://download.blender.org/release/Blender${MAJOR_MINOR}/" | \
                grep -oP "blender-\K${MAJOR_MINOR}\.\d+" | sort -V | tail -1); \
        fi && \
        echo "Installing Blender ${BLENDER_VERSION} (amd64 tarball)" && \
        cd /opt && \
        wget "https://download.blender.org/release/Blender${BLENDER_VERSION%.*}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
        tar xvf "blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
        rm "blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
        ln -s "/opt/blender-${BLENDER_VERSION}-linux-x64/blender" /usr/bin/blender && \
        cp "/opt/blender-${BLENDER_VERSION}-linux-x64/blender.desktop" /usr/share/applications && \
        cp "/opt/blender-${BLENDER_VERSION}-linux-x64/blender.svg" /usr/share/icons/hicolor/scalable/apps/; \
    else \
        echo "Installing Blender from Ubuntu noble apt (arm64; no official Linux arm64 tarball exists)" && \
        apt-get update && \
        apt-get install --no-install-recommends -y blender && \
        apt-get clean && rm -rf /var/lib/apt/lists/*; \
    fi


# GPU benchmarks
RUN \
    set -euo pipefail && \
    apt-get update && apt-get install --no-install-recommends -y glmark2

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

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
