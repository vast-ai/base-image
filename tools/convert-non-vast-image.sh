#!/bin/bash

set -euo pipefail

# Host CPU architecture (amd64 / arm64). Used to pick the right release
# artifacts for cloudflared and syncthing below. dpkg returns the same
# token shape upstream uses in their release URLs, so substituting it
# directly works for both. Querying dpkg here (rather than relying on
# the caller having `ARG TARGETARCH` declared and exported) keeps this
# script self-sufficient regardless of how the calling Dockerfile is
# structured.
ARCH="$(dpkg --print-architecture)"

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

# Enable expect/unbuffer to find its Tcl libraries (multiarch-safe, matches base Dockerfile)
mkdir -p /usr/lib/tcltk && ln -sf "/usr/lib/tcltk/$(uname -m)-linux-gnu" /usr/lib/tcltk/default

# Ensure system pip
if ! which pip > /dev/null 2>&1 || ! which pip3 > /dev/null 2>&1; then
    apt-get install --no-install-recommends -y python3-pip
fi

# Ensure a SYSTEM python3 at the absolute path, not merely a python3 on PATH.
# /opt/instance-tools/bin/vastai is `cd /opt/vast-cli && /usr/bin/python3 vast.py`
# — hardcoded — so on a base whose only interpreter is elsewhere (a conda image,
# say) the vastai CLI is broken, and with it the provisioner's failure/webhook
# path. That is silent until something goes wrong, which is the worst time to
# find out. The block above does not cover it: a conda base ships its own pip, so
# the python3-pip install is skipped and /usr/bin/python3 never appears.
# base/11-instance-metadata.sh asserts this; this is what makes the assertion
# satisfiable rather than just a report.
if [[ ! -x /usr/bin/python3 ]]; then
    apt-get install --no-install-recommends -y python3
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

wget -O /opt/portal-aio/tunnel_manager/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}
chmod +x /opt/portal-aio/tunnel_manager/cloudflared
# Make these portal-provided tools easily reachable
ln -s /opt/portal-aio/caddy_manager/caddy /opt/instance-tools/bin/caddy
ln -s /opt/portal-aio/tunnel_manager/cloudflared /opt/instance-tools/bin/cloudflared

cd /opt
git clone https://github.com/vast-ai/vast-cli
# console.vast.ai occasionally returns a transient 403 (edge/WAF blip) for
# this static cert; without retries a single bad response aborts the whole
# build under `set -euo pipefail`. Retry a few times before giving up.
wget --tries=5 --retry-connrefused --waitretry=5 --timeout=30 \
    --retry-on-http-error=403,429,500,502,503,504 \
    -O /usr/local/share/ca-certificates/jvastai.crt \
    https://console.vast.ai/static/jvastai_root.cer
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

# Set up /venv/main on the engine's own interpreter (ADR 0048).
#
# The image DECLARES which interpreter runs its engine (VAST_ENGINE_PYTHON, set in its
# Dockerfile before this script runs; "none" when there is no Python engine). It is never
# guessed from PATH: the old guess took the first python3 found and, on sglang, found the
# engine's /opt/sglang venv, which uv then resolved to its base python -- so /venv/main
# inherited /usr/bin and never saw a single sglang package.
#
# /venv/main is a plain venv (no system-site inheritance) whose site-packages is a per-file
# symlink mirror of the engine's: uv does not count INHERITED packages as installed
# (astral-sh/uv#4466), so an inheriting venv re-resolved every install as if the engine
# were absent and laid a second torch over it. The mirror is built BEFORE anything is
# installed into the venv, so every install below resolves against the engine's stack.
# 37-sync-environment.sh detects pyvenv.cfg and syncs it via tar, as before.
if [[ -z "${VAST_ENGINE_PYTHON:-}" ]]; then
    echo "FATAL: VAST_ENGINE_PYTHON is not set. Declare the engine's interpreter in the" >&2
    echo "       Dockerfile before this script (an absolute path, or 'none') -- ADR 0048." >&2
    exit 1
fi
if [[ ! -d /venv/main ]]; then
    if [[ "$VAST_ENGINE_PYTHON" == none ]]; then
        SYS_PYTHON="$(which -a python3 | grep -v /opt/sys-venv/ | head -1)"
    else
        [[ -x "$VAST_ENGINE_PYTHON" ]] || { echo "FATAL: VAST_ENGINE_PYTHON=$VAST_ENGINE_PYTHON is not executable" >&2; exit 1; }
        # The venv must run on the engine's BASE interpreter, or the mirrored extensions
        # would be loaded by a different python (venv-mirror refuses a mismatch).
        SYS_PYTHON="$("$VAST_ENGINE_PYTHON" -c 'import os, sys; print(os.path.realpath(getattr(sys, "_base_executable", sys.executable)))')"
    fi
    if [[ -n "$SYS_PYTHON" ]]; then
        # Install miniforge3 (available for users who want conda envs)
        curl -L -o /tmp/miniforge3.sh \
            "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname)-$(uname -m).sh"
        bash /tmp/miniforge3.sh -b -p /opt/miniforge3
        rm -f /tmp/miniforge3.sh

        # Configure conda (same as base image)
        /opt/miniforge3/bin/conda config --set auto_activate_base false
        /opt/miniforge3/bin/conda config --set always_copy true
        /opt/miniforge3/bin/conda config --set pip_interop_enabled true
        /opt/miniforge3/bin/conda config --add envs_dirs /venv
        /opt/miniforge3/bin/conda config --set env_prompt '({name}) '
        /opt/miniforge3/bin/conda init
        if id -u user > /dev/null 2>&1; then
            su -l user -c "/opt/miniforge3/bin/conda config --set auto_activate_base false"
            su -l user -c "/opt/miniforge3/bin/conda config --set always_copy true"
            su -l user -c "/opt/miniforge3/bin/conda config --set pip_interop_enabled true"
            su -l user -c "/opt/miniforge3/bin/conda config --add envs_dirs /venv"
            su -l user -c "/opt/miniforge3/bin/conda config --set env_prompt '({name}) '"
            su -l user -c "/opt/miniforge3/bin/conda init"
        fi
        /opt/miniforge3/bin/conda clean -ay

        mkdir -p /venv
        uv venv --relocatable --seed -p "$SYS_PYTHON" /venv/main
        if [[ "$VAST_ENGINE_PYTHON" != none ]]; then
            /opt/instance-tools/bin/venv-mirror build --venv /venv/main \
                --engine-python "$VAST_ENGINE_PYTHON"
        fi

        # Install ipykernel for Jupyter kernel registration. After the mirror, so it adds
        # only what the engine does not already provide (before it, uv duplicated numpy,
        # pydantic and 30 more over the engine's copies).
        uv pip install --python /venv/main/bin/python ipykernel
    else
        echo "WARNING: No system Python found, skipping /venv/main creation"
        rm -f /etc/vast_boot.d/37-sync-environment.sh
    fi
fi

# Create 'user' account (matches base image: uid 1001, gid 0) if not present
if ! id -u user > /dev/null 2>&1; then
    useradd -ms /bin/bash user -u 1001 -g 0
fi

rm -f /etc/vast_boot.d/48-venv-backup.sh
rm -f /opt/instance-tools/bin/venv_backup.sh
rm -f /etc/supervisor/conf.d/tensorboard.conf
rm -f /opt/supervisor-scripts/tensorboard.sh

# Install Syncthing
SYNCTHING_VERSION="$(curl -fsSL "https://api.github.com/repos/syncthing/syncthing/releases/latest" | jq -r '.tag_name' | sed 's/[^0-9\.\-]*//g')"
SYNCTHING_URL="https://github.com/syncthing/syncthing/releases/download/v${SYNCTHING_VERSION}/syncthing-linux-${ARCH}-v${SYNCTHING_VERSION}.tar.gz"
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
