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

# Carries no default ON PURPOSE, the same reason ADR 0018 records for
# LLAMA_CPP_VERSION: the image tag is DERIVED from this version, so an unpinned
# install would publish a tag that does not describe its own contents.
# build-gliner.yml always supplies it, resolving the latest gliner2 from PyPI.
ARG GLINER2_VERSION

RUN \
    set -euo pipefail && \
    [[ -n "${GLINER2_VERSION}" ]] || { echo "Must specify GLINER2_VERSION" && exit 1; } && \
    . /venv/main/bin/activate && \
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    # gliner2's [local] extra carries torch, transformers, safetensors and peft.
    # Without it the library installs with no runtime at all and can load no model
    # -- the plain "gliner2" package moved those out of the default install in
    # 2.0.0. The base image is the source of truth for torch, so the assertion
    # below catches any attempt by the extra to move it.
    #
    # NOTE: this install DOWNGRADES huggingface-hub (1.30.0 -> 0.36.2 against the
    # 2026-09-08 base), because transformers 4.57.6 caps it below 1.0. That is
    # upstream's constraint, not a choice here, and the torch assertion does not
    # cover it. GLiNER is the only consumer in this image, so nothing else depends
    # on the newer hub API -- but it is stated rather than left to be discovered.
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
