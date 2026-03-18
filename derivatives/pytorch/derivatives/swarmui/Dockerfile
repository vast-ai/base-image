ARG PYTORCH_BASE=vastai/pytorch:2.5.1-cuda-12.1.1

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Stable Diffusion SwarmUI image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG SWARMUI_REF

# We will be using our existing venv
ENV DOTNET_ROOT=/opt/dotnet
ENV SWARM_NO_VENV=1
RUN \
    set -euo pipefail && \
    [[ -n "${SWARMUI_REF}" ]] || { echo "Must specify SWARMUI_REF"; exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Install xformers while pinning to the inherited torch version.  Fail build on dependency resolution if matching version is unavailable
    pip install xformers torch==$PYTORCH_VERSION --index-url "${PYTORCH_INDEX_URL}" && \
    # Get SwarmUI and install dependencies
    cd /opt/workspace-internal/ && \
    git clone https://github.com/mcmonkeyprojects/SwarmUI && \
    cd /opt/workspace-internal/SwarmUI && \
    git checkout "${SWARMUI_REF}" && \
    rm -f launchtools/dotnet-install.sh && \
    wget https://dot.net/v1/dotnet-install.sh -O launchtools/dotnet-install.sh && \
    chmod +x launchtools/dotnet-install.sh && \
    ./launchtools/dotnet-install.sh --channel 8.0 --runtime aspnetcore --install-dir ${DOTNET_ROOT} && \
    ./launchtools/dotnet-install.sh --channel 8.0 --install-dir ${DOTNET_ROOT} && \
    ln -s ${DOTNET_ROOT}/dotnet /usr/bin/dotnet && \
    timeout 120 bash -c '\
        set -euo pipefail && \
        . /venv/main/bin/activate && \
        ./launch-linux.sh --launch_mode none > /tmp/install-swarmui.log 2>&1 & \
        installer_pid=$! && \
        until grep -q "Program is running" /tmp/install-swarmui.log; do echo "Waiting for SwarmUI installation..."; sleep 5; done && \
        kill -9 $installer_pid' || \
        { echo "Installation timed out after 120 seconds"; exit 1; } && \
    # Fix animation issues - SwarmUi does not install this automatically
    pip install imageio_ffmpeg && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash