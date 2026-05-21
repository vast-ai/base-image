ARG PYTORCH_BASE

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Open WebUI + Ollama image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG OPENWEBUI_REF
ARG OLLAMA_REF

# Do not expose this port.  Exposed and proxied port is 11434
ENV OLLAMA_HOST="0.0.0.0:21434"

RUN \
    set -euo pipefail && \
    [[ -n "${OPENWEBUI_REF}" ]] || { echo "Must specify OPENWEBUI_REF" && exit 1; } && \
    [[ -n "${OLLAMA_REF}" ]] || { echo "Must specify OLLAMA_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    # dpkg --print-architecture reflects the actual container arch — unlike
    # ${TARGETARCH:-amd64}, it doesn't silently mis-select amd64 when buildx
    # hasn't injected TARGETARCH. Used both for the xformers gate (amd64-only
    # CUDA wheels) and to pick the right Ollama release tarball.
    ARCH="$(dpkg --print-architecture)" && \
    # xformers publishes amd64-only CUDA wheels; skip on other arches
    # (e.g. aarch64/sbsa for Grace Blackwell) so dependency resolution does
    # not fail the build.
    EXTRA_ACCEL_PKGS="" && \
    if [[ "${ARCH}" == "amd64" ]]; then \
        EXTRA_ACCEL_PKGS="xformers"; \
    fi && \
    # Pin to the inherited torch version. Fail build on dependency resolution if matching version is unavailable
    uv pip install ${EXTRA_ACCEL_PKGS} torch==$PYTORCH_VERSION --index-url "${PYTORCH_INDEX_URL}" && \
    apt-get update && \
    apt-get install -y curl libcurl4-openssl-dev && \
    uv pip install onnxruntime-gpu && \
    # Get open-webui
    uv pip install --no-cache-dir open-webui==${OPENWEBUI_REF} && \
    # Get Ollama
    wget -O /tmp/ollama.tgz https://github.com/ollama/ollama/releases/download/${OLLAMA_REF}/ollama-linux-${ARCH}.tgz && \
    tar -C /usr -xzf /tmp/ollama.tgz && \
    rm -f /tmp/ollama.tgz && \
    # Ensure Ollama data persists on the workspace - WebUI directory is created on startup
    mkdir -p /opt/workspace-internal/ollama && \
    # Get Llama.cpp
    mkdir -p /opt/llama.cpp/bin && \
    cd /tmp && \
    git clone https://github.com/ggerganov/llama.cpp && \
    cmake llama.cpp -B /tmp/llama.cpp/build \
        -DBUILD_SHARED_LIBS=OFF -DGGML_CUDA=ON -DLLAMA_CURL=ON && \
    cmake --build /tmp/llama.cpp/build --config Release -j --clean-first --target llama-quantize llama-cli llama-server llama-gguf-split && \
    cp /tmp/llama.cpp/build/bin/llama-* /opt/llama.cpp/bin && \
    rm -rf /tmp/llama.cpp && \
    mkdir -p /opt/workspace-internal/llama.cpp/models/ && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; } && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="${PATH}:/opt/llama.cpp/bin"

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
