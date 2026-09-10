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

# Siblings pin the app to a git ref they clone; this one has nothing to clone, so
# the PyPI version is the equivalent pin. Defaulted rather than required because
# there is no build workflow to supply it -- a bare ARG would make the directory
# unbuildable by hand. 2.0.0 is the version validated against the 2.5 checkpoints.
ARG GLINER2_VERSION=2.0.0

RUN \
    set -euo pipefail && \
    . /venv/main/bin/activate && \
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    # gliner2's [local] extra carries torch, transformers, safetensors and peft.
    # Without it the library installs with no runtime at all and can load no model
    # -- the plain "gliner2" package moved those out of the default install in
    # 2.0.0. The base image is the source of truth for torch, so the assertion
    # below catches any attempt by the extra to move it.
    uv pip install --no-cache-dir \
        "gliner2[local]==${GLINER2_VERSION}" \
        protobuf \
        sentencepiece \
        'fastapi>=0.100.0' \
        'uvicorn>=0.23.0' \
        'pydantic>=2.0.0' && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
