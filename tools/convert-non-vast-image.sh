#!/bin/bash

set -euo pipefail

# Install a reasonable set of packages over the source image
apt-get update
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
    fonts-ubuntu \
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
    nvtop \
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
    netcat-traditional \
    net-tools \
    dnsutils \
    iproute2 \
    iputils-ping \
    traceroute \
    dos2unix \
    rsync \
    rclone \
    zip \
    unzip \
    xz-utils \
    zstd \
    linux-tools-common \
    cron \
    rsyslog

# Ensure uv python is available
if ! which uv 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
    chmod +x /tmp/uv-install.sh
    UV_UNMANAGED_INSTALL=/usr/local/bin /tmp/uv-install.sh
    rm -f /tmp/uv-install.sh 
fi

# Install Instance Portal
chown -R 0:0 /opt/portal-aio
set -euo pipefail
uv venv --seed /opt/portal-aio/venv -p 3.11
mkdir -m 770 -p /var/log/portal
chown 0:0 /var/log/portal/
mkdir -p /opt/instance-tools/bin/
. /opt/portal-aio/venv/bin/activate
uv pip install -r /opt/portal-aio/requirements.txt
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
pip install --no-cache-dir --ignore-installed \
    jupyter \
    supervisor \
    magic-wormhole
mkdir -p /var/log/supervisor

# Remove redundant base image files
[[ -d /venv/main ]] || rm -f /etc/vast_boot.d/37-sync-environment.sh

if [[ ! -d /home/user ]]; then
    rm -f /etc/vast_boot.d/46-user-propagate-ssh-keys.sh
    rm -f /etc/vast_boot.d/47-user-git-safe-dirs.sh
    rm -f /opt/instance-tools/bin/propagate_ssh_keys.sh
fi

rm -f /etc/vast_boot.d/48-venv-backup.sh
rm -f /opt/instance-tools/bin/venv_backup.sh
rm -f /etc/supervisor/conf.d/syncthing.conf
rm -f /etc/supervisor/conf.d/tensorboard.conf

# Clean up
apt-get clean && \
rm -rf /var/lib/apt/lists/*
