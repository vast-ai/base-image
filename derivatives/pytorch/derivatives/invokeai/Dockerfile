ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312-2026-10-23

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
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    # Install InvokeAI. Its package pins fastapi but not starlette, and fastapi 0.141 accepts starlette 1.x,
    # whose gzip internals break InvokeAI's GZipMiddleware override: every streamed response then fails with
    # "Expected ASGI message 'http.response.start'". InvokeAI's own uv.lock uses starlette 0.48.
    uv pip install --no-cache-dir invokeai==${INVOKEAI_VERSION} 'starlette<1' && \
    # Patchmatch will likely fail to build if the python version is not 3.12 or 3.10
    apt-get update && apt-get install --no-install-recommends -y python3-opencv libopencv-dev && \
    pip install pypatchmatch && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# CA data that does not follow the OS store on its own, after every install above (ADR 0058).
RUN /opt/instance-tools/lib/refresh-ca-data.sh

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
