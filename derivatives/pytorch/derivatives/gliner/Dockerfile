# GLiNER2 is a pip library plus HuggingFace weights -- upstream (Fastino) publishes
# no server and no Docker image, so unlike its siblings there is no repository to
# clone. The FastAPI server is vendored in ROOT/opt/workspace-internal/gliner/.
ARG PYTORCH_BASE=vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-09-08

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="GLiNER2 image (entity extraction API) suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration, startup scripts and the vendored server
COPY ./ROOT /

RUN \
    set -euo pipefail && \
    . /venv/main/bin/activate && \
    # Record pre-install PyTorch version
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # gliner2's [local] extra carries torch, transformers, safetensors and peft.
    # Without it the library installs with no runtime at all and can load no model
    # -- the plain "gliner2" package moved those out of the default install in
    # 2.0.0. The base image is the source of truth for torch, so the assertion
    # below catches any attempt by the extra to move it.
    uv pip install --no-cache-dir \
        'gliner2[local]>=2.0.0' \
        protobuf \
        sentencepiece \
        'fastapi>=0.100.0' \
        'uvicorn>=0.23.0' \
        'pydantic>=2.0.0' && \
    # Verify PyTorch version unchanged
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || \
        { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
