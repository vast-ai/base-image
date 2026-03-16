#!/bin/bash

set -euo pipefail

# Install a reasonable set of packages over the source image
apt-get update

# Detect distro — some packages are Ubuntu-specific
DISTRO_ID=$(. /etc/os-release && echo "${ID}")

# Packages available on both Debian and Ubuntu
apt-get install --no-install-recommends -y \
    acl \
    bc \
    ca-certificates \
    gpg-agent \
    software-properties-common \
    locales \
    lsb-release \
    curl \
    wget \
    sudo \
    moreutils \
    nano \
    vim \
    less \
    jq \
    git \
    git-lfs \
    man \
    tzdata \
    fonts-dejavu \
    fonts-freefont-ttf \
    ffmpeg \
    libgl1 \
    libglx-mesa0 \
    htop \
    iotop \
    strace \
    libtcmalloc-minimal4 \
    lsof \
    procps \
    psmisc \
    rdma-core \
    libibverbs1 \
    ibverbs-providers \
    libibumad3 \
    librdmacm1 \
    infiniband-diags \
    build-essential \
    cmake \
    ninja-build \
    gdb \
    libssl-dev \
    pkg-config \
    autoconf \
    automake \
    libtool \
    libffi-dev \
    libcurl4-openssl-dev \
    libxml2-dev \
    libsqlite3-dev \
    libpng-dev \
    libjpeg-dev \
    libwebp-dev \
    netcat-traditional \
    net-tools \
    dnsutils \
    iproute2 \
    iputils-ping \
    traceroute \
    dos2unix \
    expect \
    rsync \
    rclone \
    zip \
    unzip \
    xz-utils \
    zstd \
    cron \
    rsyslog

# Distro-specific packages
if [[ "$DISTRO_ID" == "ubuntu" ]]; then
    apt-get install --no-install-recommends -y \
        fonts-ubuntu \
        nvtop \
        linux-tools-common
else
    # Debian: fonts-ubuntu and linux-tools-common don't exist; nvtop may be in backports
    if apt-cache show nvtop > /dev/null 2>&1; then
        apt-get install --no-install-recommends -y nvtop
    else
        echo "nvtop not available in configured repositories — skipping"
    fi
fi

# Ensure system pip
if ! which pip > /dev/null 2>&1 || ! which pip3 > /dev/null 2>&1; then
    apt-get install --no-install-recommends -y python3-pip
fi

# Ensure uv python is available
if ! which uv > /dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
    chmod +x /tmp/uv-install.sh
    UV_UNMANAGED_INSTALL=/usr/local/bin /tmp/uv-install.sh
    rm -f /tmp/uv-install.sh 
fi

# Install Instance Portal
chown -R 0:0 /opt/portal-aio
uv venv --seed /opt/portal-aio/venv -p 3.11
mkdir -m 770 -p /var/log/portal
chown 0:0 /var/log/portal/
mkdir -p /opt/instance-tools/bin/
. /opt/portal-aio/venv/bin/activate
uv pip install -r /opt/portal-aio/requirements.txt
deactivate

# Install the declarative provisioner into its own venv
uv venv --seed /opt/instance-tools/provisioner/venv -p 3.11
. /opt/instance-tools/provisioner/venv/bin/activate
uv pip install -r /opt/instance-tools/lib/provisioner/requirements.txt
deactivate

wget -O /opt/portal-aio/tunnel_manager/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${TARGETARCH}
chmod +x /opt/portal-aio/tunnel_manager/cloudflared
# Make these portal-provided tools easily reachable
ln -s /opt/portal-aio/caddy_manager/caddy /opt/instance-tools/bin/caddy
ln -s /opt/portal-aio/tunnel_manager/cloudflared /opt/instance-tools/bin/cloudflared

cd /opt
git clone https://github.com/vast-ai/vast-cli
wget -O /usr/local/share/ca-certificates/jvastai.crt https://console.vast.ai/static/jvastai_root.cer
update-ca-certificates

# Protect the system python directory when Vast bootstrapping adds jupyter.
# Everything is installed into an isolated venv, then a shim directory at
# the front of PATH exposes all binaries (including python/pip).  At boot,
# 10-prep-env.sh removes the python/pip shims so the image's own
# interpreters take over while tools like jupyter and supervisord remain
# reachable.
uv venv -p 3.12 --seed /opt/sys-venv
VIRTUAL_ENV=/opt/sys-venv uv pip install --no-cache-dir \
    jupyter \
    tornado \
    notebook \
    jupyterlab \
    bash_kernel \
    ipython \
    ipywidgets \
    jupyter_http_over_ws \
    widgetsnbextension \
    supervisor \
    magic-wormhole
mkdir -p /var/log/supervisor

# Create a shim bin directory with symlinks to every sys-venv binary.
# The Dockerfile adds /opt/sys-venv/shim to the front of PATH so that
# during Vast bootstrap pip/python resolve here (installing into sys-venv).
mkdir -p /opt/sys-venv/shim
for bin in /opt/sys-venv/bin/*; do
    ln -sf "$bin" "/opt/sys-venv/shim/$(basename "$bin")"
done

# Remove redundant base image files
[[ ! -d /venv/main ]] && rm -f /etc/vast_boot.d/37-sync-environment.sh

# Create 'user' account (matches base image: uid 1001, gid 0) if not present
if ! id -u user > /dev/null 2>&1; then
    useradd -ms /bin/bash user -u 1001 -g 0
fi

rm -f /etc/vast_boot.d/48-venv-backup.sh
rm -f /opt/instance-tools/bin/venv_backup.sh
rm -f /etc/supervisor/conf.d/tensorboard.conf

# Install Syncthing
SYNCTHING_VERSION="$(curl -fsSL "https://api.github.com/repos/syncthing/syncthing/releases/latest" | jq -r '.tag_name' | sed 's/[^0-9\.\-]*//g')"
SYNCTHING_URL="https://github.com/syncthing/syncthing/releases/download/v${SYNCTHING_VERSION}/syncthing-linux-${TARGETARCH}-v${SYNCTHING_VERSION}.tar.gz"
mkdir -p /opt/syncthing/config /opt/syncthing/data
wget -O /opt/syncthing.tar.gz "$SYNCTHING_URL"
(cd /opt && tar -zxf syncthing.tar.gz -C /opt/syncthing/ --strip-components=1)
if id -u user > /dev/null 2>&1; then
    chown -R user:root /opt/syncthing
fi
rm -f /opt/syncthing.tar.gz

# Clean up
apt-get clean && \
rm -rf /var/lib/apt/lists/*
