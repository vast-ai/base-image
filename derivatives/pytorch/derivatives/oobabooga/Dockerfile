ARG PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312-2026-03-19

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Oobabooga Text Generation WebUI image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG OOBABOOGA_REF

RUN \
    set -euo pipefail && \
    [[ -n "${OOBABOOGA_REF}" ]] || { echo "Must specify OOBABOOGA_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Install xformers while pinning to the inherited torch version.  Fail build on dependency resolution if matching version is unavailable
    uv pip install --no-cache-dir xformers torch=="${PYTORCH_VERSION}" --torch-backend "${PYTORCH_BACKEND}" && \
    # Get Oobabooga and install dependencies
    cd /opt/workspace-internal && \
    git clone https://github.com/oobabooga/text-generation-webui && \
    cd /opt/workspace-internal/text-generation-webui && \
    git checkout "${OOBABOOGA_REF}" && \
    # Upstream requirements pin CUDA wheels to cp313 — rewrite for cp312
    sed -i 's/cp313-cp313/cp312-cp312/g; s/python_version == "3.13"/python_version == "3.12"/g' \
        requirements/full/requirements.txt && \
    uv pip install --no-cache-dir \
        -r requirements/full/requirements.txt && \
    # Download custom models into the build if you want them - None included in default build
    # wget -O ${DATA_DIRECTORY}text-generation-webui/models/model https://url.to.files/model2 && \
    # wget -O ${DATA_DIRECTORY}text-generation-webui/models/Stable-diffusion/model2.safetensors https://url.to.files/model2 && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
