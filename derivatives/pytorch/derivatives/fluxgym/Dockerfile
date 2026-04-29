ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py310-2026-04-15

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="FluxGym image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Ensure uv pulls CUDA 12.8 wheels for torch ecosystem packages
ENV UV_TORCH_BACKEND=cu128

# Required or we will not build
ARG FLUXGYM_REPO=https://github.com/cocktailpeanut/fluxgym
ARG FLUXGYM_REF
ARG SD_SCRIPTS_REPO=https://github.com/kohya-ss/sd-scripts
ARG SD_SCRIPTS_REF=sd3

RUN \
    set -euo pipefail && \
    [[ -n "${FLUXGYM_REF}" ]] || { echo "Must specify FLUXGYM_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Clone FluxGym
    cd /opt/workspace-internal/ && \
    git clone "${FLUXGYM_REPO}" fluxgym && \
    cd fluxgym && \
    git checkout "${FLUXGYM_REF}" && \
    # Clone Kohya sd-scripts (sd3 branch) as a sibling tree inside fluxgym
    git clone -b "${SD_SCRIPTS_REF}" "${SD_SCRIPTS_REPO}" sd-scripts && \
    # Install Kohya sd-scripts requirements first (fluxgym imports from it at runtime)
    (cd sd-scripts && uv pip install --no-cache-dir -r requirements.txt) && \
    # Install FluxGym requirements with pinned ecosystem versions.
    # transformers==4.49.0 and peft<0.18 are load-bearing (auto-caption and training regressions otherwise)
    uv pip install --no-cache-dir \
        torch torchvision torchaudio bitsandbytes \
        -r requirements.txt \
        'peft<0.18' \
        --torch-backend cu128 && \
    uv pip install --no-cache-dir transformers==4.49.0 && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || \
        { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
