ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="InvokeAI image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

ARG INVOKEAI_VERSION=latest

RUN \
    set -euo pipefail && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Install InvokeAI
    if [[ "${INVOKEAI_VERSION}" = "latest" ]]; then \
        uv pip install invokeai pydantic==2.11.7 || uv pip install invokeai; \
    else \
        uv pip install invokeai==${INVOKEAI_VERSION} pydantic==2.11.7 || uv pip install invokeai==${INVOKEAI_VERSION}; \
    fi && \
    # Patchmatch will likely fail to build if the python version is not 3.12 or 3.10, but it is non-fatal
    apt-get update && apt-get install --no-install-recommends -y python3-opencv libopencv-dev && \
    pip install pypatchmatch || true && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
