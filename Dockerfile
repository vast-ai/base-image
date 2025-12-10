# Choose a base image.  Sensible options include ubuntu:xx.xx, nvidia/cuda:xx-cuddnx
ARG BASE_IMAGE

### Build Caddy with single port TLS redirect
FROM --platform=$BUILDPLATFORM golang:1.23.4-bookworm AS caddy_builder

# Install xcaddy for the current architecture
RUN go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest

# Build Caddy
ENV CGO_ENABLED=0
ARG TARGETARCH
RUN GOOS=linux GOARCH=$TARGETARCH xcaddy build \
    --with github.com/caddyserver/caddy/v2=github.com/ai-dock/caddy/v2@httpredirect \
    --with github.com/caddyserver/replace-response

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

# Vast.ai environment variables used for Jupyter & Data sync
ENV DATA_DIRECTORY=/workspace
ENV WORKSPACE=/workspace

# Ubuntu 24.04 requires this for compatibility with our /.launch script
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# Don't ask questions we cannot answer during the build
ENV DEBIAN_FRONTEND=noninteractive
# Allow immediate output
ENV PYTHONUNBUFFERED=1

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
        # Display
        fonts-dejavu \
        fonts-freefont-ttf \
        fonts-ubuntu \
        ffmpeg \
        libgl1 \
        libglx-mesa0 \
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
        ocl-icd-dev \
        ocl-icd-opencl-dev \
        # Vulkan
        libvulkan1 \
        vulkan-tools && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

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
    rm -f /tmp/uv-install.sh

# Install Extra Nvidia packages (OpenCL, GL, Nvenc)
# When installing libnvidia packages always pick the earliest version to avoid mismatched libs
# We cannot know the runtime driver version so we aim for best compatibility 
ARG TARGETARCH
RUN \
    set -euo pipefail && \
    apt-get update && \
    if command -v rocm-smi >/dev/null 2>&1; then \
        apt-get install -y rocm-opencl-runtime; \
    elif [[ -n "${CUDA_VERSION:-}" ]]; then \
        CUDA_MAJOR_MINOR=$(echo "${CUDA_VERSION}" | cut -d. -f1,2 | tr -d ".") && \
        case "${CUDA_MAJOR_MINOR}" in \
            "118") driver_version=450 ;; \
            "120") driver_version=525 ;; \
            "121") driver_version=530 ;; \
            "122") driver_version=535 ;; \
            "123") driver_version=545 ;; \
            "124") driver_version=550 ;; \
            "125") driver_version=555 ;; \
            "126") driver_version=560 ;; \
            "127") driver_version=565 ;; \
            "128") driver_version=570 ;; \
            "129") driver_version=575 ;; \
            "130") driver_version=580 ;; \
            "131") driver_version=590 ;; \
        esac; \
        if [[ -n "${driver_version:-}" ]]; then \
            # decode is available for all architectures
            earliest_version=$(apt-cache madison "libnvidia-decode-${driver_version}" | awk '{print $3}' | sort -V | head -n1 || true); \
            if [[ -n "${earliest_version:-}" ]]; then \
                echo "Package: libnvidia-*-${driver_version}" > /etc/apt/preferences.d/nvidia-pin; \
                echo "Pin: version $earliest_version" >> /etc/apt/preferences.d/nvidia-pin; \
                echo "Pin-Priority: 1001" >> /etc/apt/preferences.d/nvidia-pin; \
                echo "" >> /etc/apt/preferences.d/nvidia-pin; \
                echo "Package: nvidia-*-${driver_version}" >> /etc/apt/preferences.d/nvidia-pin; \
                echo "Pin: version $earliest_version" >> /etc/apt/preferences.d/nvidia-pin; \
                echo "Pin-Priority: 1001" >> /etc/apt/preferences.d/nvidia-pin; \
                for pkg in nvidia-utils libnvidia-gl libnvidia-cfg1 libnvidia-compute libnvidia-decode libnvidia-encode; do \
                    apt-get install -y "${pkg}-${driver_version}" 2>/dev/null || true; \
                done; \
            fi; \
        fi; \
    fi && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install NVM for node version management
RUN \
    set -euo pipefail && \
    git clone https://github.com/nvm-sh/nvm.git /opt/nvm && \
    (cd /opt/nvm/ && git checkout `git describe --abbrev=0 --tags --match "v[0-9]*" $(git rev-list --tags --max-count=1)`) && \
    source /opt/nvm/nvm.sh && \
    nvm install --lts

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
    wget -O /opt/portal-aio/tunnel_manager/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${TARGETARCH} && \
    chmod +x /opt/portal-aio/tunnel_manager/cloudflared && \
    # Make these portal-provided tools easily reachable
    ln -s /opt/portal-aio/caddy_manager/caddy /opt/instance-tools/bin/caddy && \
    ln -s /opt/portal-aio/tunnel_manager/cloudflared /opt/instance-tools/bin/cloudflared && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Populate the system Python environment with useful tools.  Add jupyter to speed up instance creation and install tensorboard as it is quite useful if training
# These are in the system and not the venv because we want that to be as clean as possible
RUN \
    set -euo pipefail && \
    cd /opt && \
    git clone https://github.com/vast-ai/vast-cli && \
    wget -O /usr/local/share/ca-certificates/jvastai.crt https://console.vast.ai/static/jvastai_root.cer && \
    update-ca-certificates && \
    pip install --no-cache-dir --ignore-installed \
        jupyter \
        supervisor \
        tensorboard \
        magic-wormhole && \
    mkdir -p /var/log/supervisor

# Install Syncthing
ARG TARGETARCH
RUN \
    set -euo pipefail && \
    SYNCTHING_VERSION="$(curl -fsSL "https://api.github.com/repos/syncthing/syncthing/releases/latest" | jq -r '.tag_name' | sed 's/[^0-9\.\-]*//g')" && \
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
    curl -L -o /tmp/miniforge3.sh "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname)-$(uname -m).sh" && \
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
    /opt/miniforge3/bin/conda clean -ay

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
        huggingface_hub[cli] \
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

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash

ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
