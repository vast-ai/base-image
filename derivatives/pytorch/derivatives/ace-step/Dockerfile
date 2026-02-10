ARG PYTORCH_BASE=vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py311

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="ACE-Step 1.5 music generation image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG ACE_STEP_REF
ARG ACE_STEP_UI_REF=main

RUN \
    set -euo pipefail && \
    [[ -n "${ACE_STEP_REF}" ]] || { echo "Must specify ACE_STEP_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    . /opt/nvm/nvm.sh && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Clone and install ACE-Step 1.5
    cd /opt/workspace-internal/ && \
    git clone https://github.com/ace-step/ACE-Step-1.5 && \
    cd ACE-Step-1.5 && \
    git checkout "${ACE_STEP_REF}" && \
    UV_PROJECT_ENVIRONMENT=/venv/main uv sync && \
    # UI is not aware of the correct venv path and looks up this dir for python
    ln -s /venv/main .venv && \
    # Clone and install ace-step-ui
    cd /opt/workspace-internal/ && \
    git clone https://github.com/fspecii/ace-step-ui && \
    cd ace-step-ui && \
    git checkout "${ACE_STEP_UI_REF}" && \
    npm install && \
    cd server && \
    npm install && \
    cp .env.example .env && \
    # Verify PyTorch version unchanged
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || \
        { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
