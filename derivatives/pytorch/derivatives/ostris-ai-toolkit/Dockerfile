ARG PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312-2026-04-15

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
    # No torchcodec strip needed: the pytorch base (torch 2.9.1) ships
    # torchcodec 0.9.1, which exactly matches ai-toolkit's pin. Aligns
    # with the upstream DGX install path (dgx_instructions.md pins torch
    # 2.9.1 too). Mismatched bases (e.g. torch 2.7.1) would silently
    # clobber the base's torchcodec via uv pip install — torchcodec's
    # PyPI metadata declares no torch requirement, and the
    # torch_version_pre/post assertion below only checks torch itself.
    # If this Dockerfile is ever re-pointed at a different torch base,
    # check torch-companions.json: torchcodec must match ai-toolkit's
    # requirements_base.txt pin to avoid the silent ABI clobber.
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
