ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="InvokeAI image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

ARG INVOKEAI_VERSION

RUN \
    set -euo pipefail && \
    [[ -n "${INVOKEAI_VERSION}" ]] || { echo "Must specify INVOKEAI_VERSION" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Install InvokeAI
    uv pip install invokeai==${INVOKEAI_VERSION} && \
    # Patchmatch will likely fail to build if the python version is not 3.12 or 3.10
    apt-get update && apt-get install --no-install-recommends -y python3-opencv libopencv-dev && \
    pip install pypatchmatch && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
