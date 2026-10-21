# Choose a base image.  Sensible options include ubuntu:xx.xx, nvidia/cuda:xx-cuddnx
ARG BASE_IMAGE

### Build Caddy: upstream at a pinned release, plus one patch (ADR 0057)
# The single-port TLS listener answers plain HTTP with a redirect to https://. Upstream sends a
# 308, which browsers cache permanently for that ip:port; Vast reuses ip:port across instances
# and HTTPS can be off, so a cached 308 strands the next tenant. We send a 307. That one line is
# the whole patch, and the build fails unless it applied.
FROM --platform=$BUILDPLATFORM golang:1.27.3-bookworm@sha256:21ade014faf952365b9ecbd8dbdb87176fbacb82fac9fb132d9f2a7b8cc3f132 AS caddy_builder

ARG CADDY_VERSION=v2.11.7
ARG CADDY_COMMIT=72dd0fb067f6d7826c7f79907670ba4a713bfe37
ARG XCADDY_VERSION=v0.4.7
ARG REPLACE_RESPONSE_COMMIT=80962887e4c680abfd1b11ce1403fa8cc0fdd3d6
# x/net above Caddy's own requirement clears five reachable advisories (GO-2026-6603/6610/6611/6612/6617).
ARG XNET_VERSION=v0.60.0

# The clone is checked against CADDY_COMMIT, so its integrity does not rest on this image's CA
# store; Go modules are checked against sumdb.
RUN set -eu && \
    apt-get update && apt-get install -y --only-upgrade ca-certificates && \
    go install github.com/caddyserver/xcaddy/cmd/xcaddy@${XCADDY_VERSION} && \
    git clone --depth 1 --branch "${CADDY_VERSION}" https://github.com/caddyserver/caddy /src/caddy && \
    [ "$(git -C /src/caddy rev-parse HEAD)" = "${CADDY_COMMIT}" ] && \
    f=/src/caddy/modules/caddyhttp/httpredirectlistener.go && \
    sed -i 's/"308 Permanent Redirect"/"307 Temporary Redirect"/; s/StatusCode: 308,/StatusCode: 307,/' "$f" && \
    grep -q '"307 Temporary Redirect"' "$f" && grep -q 'StatusCode: 307,' "$f" && ! grep -q '308' "$f"

ENV CGO_ENABLED=0
ARG TARGETARCH
RUN cd /src && GOOS=linux GOARCH=$TARGETARCH xcaddy build "${CADDY_VERSION}" \
    --output /go/caddy \
    --with github.com/caddyserver/caddy/v2=/src/caddy \
    --with github.com/caddyserver/replace-response@${REPLACE_RESPONSE_COMMIT} \
    --replace golang.org/x/net=golang.org/x/net@${XNET_VERSION}

### Main Build ###

FROM ${BASE_IMAGE} AS main_build

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Base image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Support pipefail so we don't build broken images
SHELL ["/bin/bash", "-c", "umask 002 && /bin/bash -c \"$@\"", "-"]
# Use umask 002 to the power 'user' can easily share the root group.
RUN sed -i '1i umask 002' /root/.bashrc

# Add some useful scripts and config files
COPY ./ROOT/ /
# Marker for tests
ENV IMAGE_TYPE=vast

# Vast.ai environment variables used for Jupyter & Data sync
ENV DATA_DIRECTORY=/workspace
ENV WORKSPACE=/workspace

# Ubuntu 24.04 requires this for compatibility with our /.launch script
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# Don't ask questions we cannot answer during the build
ENV DEBIAN_FRONTEND=noninteractive
# Allow immediate output
ENV PYTHONUNBUFFERED=1
# Enable expect/unbuffer to find its Tcl libraries (multiarch-safe)
RUN mkdir -p /usr/lib/tcltk && ln -sf /usr/lib/tcltk/$(uname -m)-linux-gnu /usr/lib/tcltk/default
ENV TCLLIBPATH=/usr/lib/tcltk/default
# Expose all toolklit features
ENV NVIDIA_DRIVER_CAPABILITIES=all

# Blackwell fix
ARG BASE_IMAGE
RUN \
    # Update libnccl for Blackwell GPUs
    set -euo pipefail && \
    if [[ "$BASE_IMAGE" == "nvidia/cuda:12.8"* ]]; then \
        NCCL_VERSION=$(dpkg-query -W -f='${Version}' libnccl2 2>/dev/null | cut -d'-' -f1 || echo "0.0.0"); \
        if dpkg --compare-versions "$NCCL_VERSION" lt "2.26.2"; then \
            apt-get -y update; \
            apt-get install -y --allow-change-held-packages libnccl2=2.26.2-1+cuda12.8 libnccl-dev=2.26.2-1+cuda12.8; \
        fi; \
    fi && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Interactive container
RUN \
    set -euo pipefail && \
    # Not present in Ubuntu 24.04
    if ! command -v unminimize >/dev/null 2>&1; then \
        apt-get update; \
        apt-get install -y --no-install-recommends unminimize; \
    fi && \
    printf "%s\n%s" y y | unminimize && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create a useful base environment with commonly used tools
ARG TARGETARCH
RUN \
    set -euo pipefail && \
    ([ $TARGETARCH = "arm64" ] && echo "Skipping i386 architecture for ARM builds" || dpkg --add-architecture i386) && \
    apt-get update && \
    apt-get upgrade -y && \
    apt-get install --no-install-recommends -y \
        # Base system utilities
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
        expect \
        # Display
        fonts-dejavu \
        fonts-freefont-ttf \
        fonts-ubuntu \
        ffmpeg \
        sox \
        mesa-utils-extra \
        # System monitoring & debugging
        htop \
        iotop \
        strace \
        libtcmalloc-minimal4 \
        lsof \
        procps \
        psmisc \
        nvtop \
        # Infiniband support (if devices mounted)
        rdma-core \
        libibverbs1 \
        ibverbs-providers \
        libibumad3 \
        librdmacm1 \
        infiniband-diags \
        # Development essentials
        build-essential \
        cmake \
        ninja-build \
        gdb \
        libssl-dev \
        # Build system tools
        pkg-config \
        autoconf \
        automake \
        libtool \
        # Common dev libraries
        libffi-dev \
        libcurl4-openssl-dev \
        libxml2-dev \
        libsqlite3-dev \
        # Image processing
        libpng-dev \
        libjpeg-dev \
        libwebp-dev \
        # System Python
        python3-full \
        python3-dev \
        python3-pip \
        # Network utilities
        netcat-traditional \
        net-tools \
        dnsutils \
        iproute2 \
        iputils-ping \
        traceroute \
        # File management
        dos2unix \
        rsync \
        rclone \
        zip \
        unzip \
        xz-utils \
        zstd \
        # Performance analysis
        linux-tools-common \
        cron \
        # Required for cron logging
        rsyslog \
        # OpenCL General
        clinfo \
        pocl-opencl-icd \
        opencl-headers \
        ocl-icd-libopencl1 \
        ocl-icd-dev \
        ocl-icd-opencl-dev \
        # Vulkan
        vulkan-tools && \
    mkdir -p /etc/OpenCL/vendors && \
    echo "libnvidia-opencl.so.1" > /etc/OpenCL/vendors/nvidia.icd && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Ensure Nvidia repos are available if using stock images
ARG TARGETARCH
RUN \
    set -euo pipefail && \
    if ! compgen -G "/etc/apt/sources.list.d/cuda*" > /dev/null && ! compgen -G "/etc/apt/sources.list.d/rocm*" > /dev/null; then \
        UBUNTU_VERSION=$(. /etc/os-release && echo "$VERSION_ID" | tr -d '.') && \
        if [[ "$TARGETARCH" = "amd64" ]]; then \
            ARCH="x86_64"; \
        elif [[ "$TARGETARCH" = "arm64" ]]; then \
            ARCH="sbsa"; \
        else \
            echo "Unsupported TARGETARCH: ${TARGETARCH}. Cannot configure Nvidia CUDA repository." >&2; \
            exit 1; \
        fi && \
        # NVIDIA's keyring package carries the repo's current signing key (26.04's differs
        # from 22.04/24.04's) and writes the source list, as NVIDIA's own images do. Its
        # apt pin is removed so stock images keep resolving packages as they did. The key
        # it installed must be the one expected for this release; a new key fails the
        # build rather than being trusted unseen.
        curl -fsSL -o /tmp/cuda-keyring.deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu${UBUNTU_VERSION}/${ARCH}/cuda-keyring_1.1-1_all.deb && \
        dpkg -i /tmp/cuda-keyring.deb && \
        rm -f /tmp/cuda-keyring.deb /etc/apt/preferences.d/cuda-repository-pin-600 && \
        CUDA_KEY_FPR=$(gpg --show-keys --with-colons /usr/share/keyrings/cuda-archive-keyring.gpg | awk -F: '/^fpr/{print $10}' | tr '\n' ' ') && \
        case "${UBUNTU_VERSION}:${CUDA_KEY_FPR% }" in \
            2204:EB693B3035CD5710E231E123A4B469963BF863CC|2404:EB693B3035CD5710E231E123A4B469963BF863CC) ;; \
            2604:14BAFBC7562AD710CA04E69905FBB6DA60DF8A40) ;; \
            *) echo "Unexpected NVIDIA repo key for ubuntu${UBUNTU_VERSION}: ${CUDA_KEY_FPR}" >&2; exit 1 ;; \
        esac && \
        apt-get update && \
        apt-get clean && \
        rm -rf /var/lib/apt/lists/*; \
    fi

# Add a normal user account - Some applications don't like to run as root so we should save our users some time.  Give it unfettered access to sudo
RUN \
    set -euo pipefail && \
    useradd -ms /bin/bash user -u 1001 -g 0 && \
    sed -i '1i umask 002' /home/user/.bashrc && \
    echo "PATH=${PATH}" >> /home/user/.bashrc && \
    echo "user ALL=(ALL) NOPASSWD:ALL" | tee /etc/sudoers.d/user && \
    sudo chmod 0440 /etc/sudoers.d/user && \
    mkdir -m 700 -p /run/user/1001 && \
    chown 1001:0 /run/user/1001 && \
    mkdir -p /run/dbus && \
    mkdir -p /opt/workspace-internal/

# Add support for uv, the excellent Python environment manager
ENV UV_CACHE_DIR=/.uv/cache
ENV UV_NO_CACHE=1
# We have disabled caching but set default to copy.  Hardlinks will lead to issues with volumes/copy tools
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_BIN_DIR=/.uv/python_bin
ENV UV_PYTHON_INSTALL_DIR=/.uv/python_install
RUN \
    set -euo pipefail && \
    mkdir -p "${UV_CACHE_DIR}" "${UV_PYTHON_BIN_DIR}" "${UV_PYTHON_INSTALL_DIR}" && \
    curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh && \
    chmod +x /tmp/uv-install.sh && \
    UV_UNMANAGED_INSTALL=/usr/local/bin /tmp/uv-install.sh && \
    rm -rf /tmp/*

# Install NVM for node version management
RUN \
    set -euo pipefail && \
    git clone https://github.com/nvm-sh/nvm.git /opt/nvm && \
    (cd /opt/nvm/ && git checkout `git describe --abbrev=0 --tags --match "v[0-9]*" $(git rev-list --tags --max-count=1)`) && \
    source /opt/nvm/nvm.sh && \
    nvm install --lts && \
    # Node compiles in its own roots, so its trust only moves with a Node release. These are the
    # first releases that block the compromised eMudhra intermediates (nodejs/node#66871); an
    # LTS line not listed here fails the build until it has one (ADR 0058).
    node -e 'const [M,m,p]=process.versions.node.split(".").map(Number);const f={22:[23,4],24:[22,1],26:[12,1]}[M];if(!f||m<f[0]||(m===f[0]&&p<f[1])){console.error(`Node ${process.version} predates the eMudhra block (need 22.23.4 / 24.22.1 / 26.12.1)`);process.exit(1)}'

# Add the 'instance portal' web app into this container to avoid needing to specify in onstart.  
# We will launch each component with supervisor - Not the standalone launch script.
COPY ./portal-aio /opt/portal-aio
COPY --from=caddy_builder /go/caddy /opt/portal-aio/caddy_manager/caddy
ARG TARGETARCH
RUN \
    chown -R 0:0 /opt/portal-aio && \
    set -euo pipefail && \
    uv venv --seed /opt/portal-aio/venv -p 3.11 && \
    mkdir -m 770 -p /var/log/portal && \
    chown 0:0 /var/log/portal/ && \
    mkdir -p opt/instance-tools/bin/ && \
    . /opt/portal-aio/venv/bin/activate && \
    uv pip install -r /opt/portal-aio/requirements.txt && \
    deactivate && \
    # Retries only. Unlike curl, wget already fails closed on an HTTP error (exit 8) and
    # does not leave the error body behind as the artifact, so this is not L092's defect —
    # what it lacked was patience through the same CDN flakiness that broke the miniforge
    # fetch. The binary it produces is contract-tested separately.
    wget --tries=5 --waitretry=2 --retry-connrefused \
        -O /opt/portal-aio/tunnel_manager/cloudflared \
        https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${TARGETARCH} && \
    chmod +x /opt/portal-aio/tunnel_manager/cloudflared && \
    # Make these portal-provided tools easily reachable
    ln -s /opt/portal-aio/caddy_manager/caddy /opt/instance-tools/bin/caddy && \
    ln -s /opt/portal-aio/tunnel_manager/cloudflared /opt/instance-tools/bin/cloudflared && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install the declarative provisioner into its own venv
RUN \
    set -euo pipefail && \
    uv venv --seed /opt/instance-tools/provisioner/venv -p 3.11 && \
    . /opt/instance-tools/provisioner/venv/bin/activate && \
    uv pip install -r /opt/instance-tools/lib/provisioner/requirements.txt && \
    deactivate && \
    # uv's managed interpreters carry the pip (and its vendored certifi) of their build date.
    for py in "${UV_PYTHON_INSTALL_DIR}"/*/bin/python3; do "$py" -m pip install --no-cache-dir -U pip; done

# Populate the system Python environment with useful tools.  Add jupyter to speed up instance creation and install tensorboard as it is quite useful if training
# These are in the system and not the venv because we want that to be as clean as possible
RUN \
    set -euo pipefail && \
    cd /opt && \
    git clone https://github.com/vast-ai/vast-cli && \
    wget --tries=5 --retry-connrefused --waitretry=5 --timeout=30 \
        --retry-on-http-error=403,429,500,502,503,504 \
        -O /usr/local/share/ca-certificates/jvastai.crt \
        https://console.vast.ai/static/jvastai_root.cer && \
    update-ca-certificates && \
    # The distrust must have landed: Ubuntu's fixed ca-certificates (20260816ubuntu0.*) drops
    # emSign ECC Root CA - G3, which anchors the compromised eMudhra intermediates.
    dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20260816 && \
    [ ! -e /etc/ssl/certs/emSign_ECC_Root_CA_-_G3.pem ] && \
    pip install --no-cache-dir --ignore-installed \
        certifi \
        jupyter \
        supervisor \
        tensorboard \
        magic-wormhole && \
    # grpcio (via tensorboard) ships its own root bundle and ignores SSL_CERT_FILE; point it at
    # the OS store, as Debian does for certifi, so it cannot go stale on its own.
    for f in $(find / -xdev -path '*/grpc/_cython/_credentials/roots.pem' -type f 2>/dev/null); do \
        ln -sf /etc/ssl/certs/ca-certificates.crt "$f"; \
    done && \
    mkdir -p /var/log/supervisor

# Install Syncthing
ARG TARGETARCH
# Resolved ONCE per run by the workflow under its GITHUB_TOKEN and passed in.
# Empty is a supported fallback, so a local or manual `docker build` still works.
#
# TWO reasons, and the second is the one that matters:
#
#  1. Robustness. Unauthenticated api.github.com allows 60 req/hr PER IP, and
#     GitHub-hosted runners share a NAT pool. One lookup per build cell x 24
#     cells is a coin flip on a busy day and a certainty during an incident —
#     measured 2026-08-17, HTTP 403 on cuda-13.0, which then skipped all twelve
#     manifest merges. Only the version LOOKUP is limited; the download is not,
#     which is why cloudflared and miniforge are unaffected (they use the
#     releases/latest/download redirect and never call the API).
#
#  2. Consistency. Each cell resolving `latest` independently means a syncthing
#     release landing mid-build — a ~40 minute window — ships SOME configs on the
#     old version and some on the new, with nothing recording which. One
#     resolution per run makes the set coherent, and the echo below is the first
#     record anywhere of which syncthing an image actually contains.
ARG SYNCTHING_VERSION=""
RUN \
    set -euo pipefail && \
    SYNCTHING_VERSION="${SYNCTHING_VERSION:-$(curl -fsSL "https://api.github.com/repos/syncthing/syncthing/releases/latest" | jq -r '.tag_name')}" && \
    SYNCTHING_VERSION="${SYNCTHING_VERSION#v}" && \
    [ -n "$SYNCTHING_VERSION" ] || { echo "could not resolve a syncthing version"; exit 1; } && \
    echo "syncthing version: ${SYNCTHING_VERSION}" && \
    SYNCTHING_URL="https://github.com/syncthing/syncthing/releases/download/v${SYNCTHING_VERSION}/syncthing-linux-${TARGETARCH}-v${SYNCTHING_VERSION}.tar.gz" && \
    mkdir -p /opt/syncthing/config && \
    mkdir -p /opt/syncthing/data && \
    wget -O /opt/syncthing.tar.gz $SYNCTHING_URL && (cd /opt && tar -zxf syncthing.tar.gz -C /opt/syncthing/ --strip-components=1) && \
    chown -R user:root /opt/syncthing && \
    rm -f /opt/syncthing.tar.gz

ARG BASE_IMAGE
ARG PYTHON_VERSION=3.10
ENV PYTHON_VERSION=${PYTHON_VERSION}

RUN \
    set -euo pipefail && \
    # -f, and it is load-bearing. Without it curl writes the HTTP ERROR BODY to the file
    # and exits 0, so `bash` runs it: on 2026-09-07 GitHub's release CDN returned 504s and
    # 7 of 25 configs died with `miniforge3.sh: line 1: `<html><body><h1>504 Gateway
    # Timeout`, a shell-syntax error several lines from the real cause (L092).
    #
    # Retries because this is the flakiest kind of dependency — a redirect to a CDN that
    # rate-limits and times out. --retry-all-errors covers the 5xx and the connection
    # resets alike; curl on the oldest base here (Ubuntu 22.04, 7.81) supports it.
    #
    # And an assertion, because -f only judges the STATUS. A 200 carrying the wrong body
    # would still be saved and executed, so check the payload is a script before running
    # it — the artifact, not the transfer.
    curl -fL --retry 5 --retry-delay 2 --retry-all-errors \
        -o /tmp/miniforge3.sh \
        "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname)-$(uname -m).sh" && \
    # BRACED, so the `||` binds to the payload check alone. Left bare it reads
    # `curl && check || FATAL`, and a curl failure would fall through to the FATAL and
    # report "not a script" for a download that never happened — a misleading message,
    # which is the very thing this change exists to stop. Braced, a curl failure just
    # ends the AND-list and `set -e` fails the RUN with curl's own error.
    { head -1 /tmp/miniforge3.sh | grep -q '^#!' || \
        { echo "FATAL: miniforge installer is not a script — the download returned something else (L092)"; head -3 /tmp/miniforge3.sh; exit 1; }; } && \
    bash /tmp/miniforge3.sh -b -p /opt/miniforge3 && \
    /opt/miniforge3/bin/conda init && \
    su -l user -c "/opt/miniforge3/bin/conda init" && \
    mkdir -p /venv && \
    /opt/miniforge3/bin/conda config --set auto_activate_base false && \
    /opt/miniforge3/bin/conda config --set always_copy true && \
    /opt/miniforge3/bin/conda config --set pip_interop_enabled true && \
    /opt/miniforge3/bin/conda config --add envs_dirs /venv && \
    /opt/miniforge3/bin/conda config --set env_prompt '({name}) ' && \
    su -l user -c "/opt/miniforge3/bin/conda config --set auto_activate_base false" && \
    su -l user -c "/opt/miniforge3/bin/conda config --set always_copy true" && \
    su -l user -c "/opt/miniforge3/bin/conda config --set pip_interop_enabled true" && \
    su -l user -c "/opt/miniforge3/bin/conda config --add envs_dirs /venv" && \
    su -l user -c "/opt/miniforge3/bin/conda config --set env_prompt '({name}) '" && \
    if [[ "$BASE_IMAGE" == *"nvidia"* ]]; then \
        /opt/miniforge3/bin/conda config --add channels nvidia; \
        su -l user -c "/opt/miniforge3/bin/conda config --add channels nvidia"; \
    fi && \
    /opt/miniforge3/bin/conda create -p /venv/main python="${PYTHON_VERSION}" -y && \
    mkdir -p /venv/main/etc/conda/{activate.d,deactivate.d} && \
    echo 'echo -e "\033[32mActivated conda/uv virtual environment at \033[36m$(realpath $CONDA_PREFIX)\033[0m"' \
        > /venv/main/etc/conda/activate.d/environment.sh && \
    # The installer bundles ca-certificates/certifi/pip as of its release; take the current
    # ones (install, not update: update refuses a package the prefix lacks), then empty the
    # package cache, whose stale copies would seed every new env.
    /opt/miniforge3/bin/conda install -y -n base ca-certificates certifi pip && \
    /opt/miniforge3/bin/conda install -y -p /venv/main ca-certificates pip && \
    /opt/miniforge3/bin/conda clean --all --force-pkgs-dirs -y && \
    rm -rf /tmp/*

# Add venv-like activation script for conda env
RUN cat <<'CONDA_ACTIVATION_SCRIPT' > /venv/main/bin/activate
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "This script must be sourced: source bin/activate"
    exit 1
fi

# Define deactivate function
deactivate() {
    # Deactivate conda environment
    if type conda &> /dev/null; then
        conda deactivate 2>/dev/null || true
    fi

    # Unset the deactivate function itself
    unset -f deactivate

    # Return success
    return 0
}

# Check if conda is properly initialized by testing for the conda shell function
# (not just the command existence)
if ! type conda &> /dev/null || ! declare -F conda &> /dev/null; then
    # Add condabin to PATH if not already there
    if [[ "$PATH" != *"/opt/miniforge3/condabin"* ]]; then
        export PATH="/opt/miniforge3/condabin:$PATH"
    fi
    
    # Source the conda shell script to load shell functions
    if [[ -f /opt/miniforge3/etc/profile.d/conda.sh ]]; then
        source /opt/miniforge3/etc/profile.d/conda.sh
    fi
fi

# Activate the conda environment
conda activate "$(realpath /venv/main)"
CONDA_ACTIVATION_SCRIPT

RUN \
    set -euo pipefail && \
    . /venv/main/bin/activate && \
    uv pip install \
        wheel \
        huggingface-hub[cli] \
        ipykernel \
        ipywidgets && \
    python -m ipykernel install \
        --name="main" \
        --display-name="Python3 (main venv)" && \
    # Re-add as default.  We don't want users accidentally installing packages in the system python
    python -m ipykernel install \
        --name="python3" \
        --display-name="Python3 (ipykernel)" && \
    deactivate && \
    /usr/bin/pip install \
        conda-pack \
        ipykernel && \
    /usr/bin/python3 -m ipykernel install \
        --name="system-python" \
        --display-name="Python3 (System)" && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV PATH=/opt/instance-tools/bin:${PATH}

# Every interpreter we ship bundles a pip wheel for `python -m venv`; replace stale ones (ADR 0058).
RUN /opt/instance-tools/lib/refresh-bundled-pip.sh

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash

ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
