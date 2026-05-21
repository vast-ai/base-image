ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312-2026-04-15

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Ostris AI Toolkit image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG AI_TOOLKIT_REPO=https://github.com/ostris/ai-toolkit
ARG AI_TOOLKIT_REF=6870ab4
RUN \
    set -euo pipefail && \
    [[ -n "${AI_TOOLKIT_REF}" ]] || { echo "Must specify AI_TOOLKIT_REF"; exit 1; } && \
    . /venv/main/bin/activate && \
    . /opt/nvm/nvm.sh && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    cd /opt/workspace-internal && \
    git clone "${AI_TOOLKIT_REPO}" && \
    cd $(basename ${AI_TOOLKIT_REPO}) && \
    git checkout "${AI_TOOLKIT_REF}" && \
    # Fixes known issue with toolkit 6870ab4
    uv pip install --no-cache-dir timm==1.0.22 && \
    # Strip upstream's torchcodec pin from requirements_base.txt before
    # installing. ai-toolkit pins torchcodec==0.9.1, which was built
    # against torch 2.10's ABI; torchcodec's PyPI metadata declares no
    # torch requirement, so without this strip uv silently replaces the
    # pytorch base's pinned torchcodec (0.5 on torch 2.7.1) with 0.9.1.
    # The torch_version_pre/post assertion below only checks torch
    # itself, not torchcodec, so the mismatch slips through to runtime.
    # ai-toolkit uses torchcodec transitively (torchaudio.load() in
    # toolkit/dataloader_mixins.py routes through torchcodec under
    # torchaudio 2.x), so keeping the base's matching torchcodec 0.5 is
    # the correct fix; the explicit pin was wrong.
    sed -i '/^torchcodec[<>=]/d' requirements_base.txt && \
    uv pip install --no-cache-dir -r requirements.txt && \
    cd ui && \
    npm install && \
    npm run update_db && \
    npm run build && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
