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
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Install xformers while pinning to the inherited torch version.  Fail build on dependency resolution if matching version is unavailable
    pip install xformers torch==$PYTORCH_VERSION --index-url "${PYTORCH_INDEX_URL}" && \
    pip install onnxruntime-gpu && \
    # Get open-webui
    pip install --no-cache-dir open-webui==${OPENWEBUI_REF} && \
    # Get Ollama
    wget -O /tmp/ollama.tgz https://github.com/ollama/ollama/releases/download/${OLLAMA_REF}/ollama-linux-${TARGETARCH:-amd64}.tgz && \
    tar -C /usr -xzf /tmp/ollama.tgz && \
    rm -f /tmp/ollama.tgz && \
    # Ensure Ollama data persists on the workspace - WebUI directory is created on startup
    mkdir -p /opt/workspace-internal/ollama && \
    # Get Llama.cpp
    mkdir -p /opt/llama.cpp/bin && \
    cd /tmp && \
    apt-get update && \
    apt-get install libcurl4-openssl-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    git clone https://github.com/ggerganov/llama.cpp && \
    cuda_int="$(echo "${CUDA_VERSION}" | cut -d. -f1,2 | tr -d '.')" && \
    if (( cuda_int >= 128 )); then \
        CMAKE_CUDA_ARCHITECTURES="75;80;86;89;90;100;120"; \
    else \
        CMAKE_CUDA_ARCHITECTURES="75;80;86;89;90"; \
    fi && \
    cmake llama.cpp -B /tmp/llama.cpp/build \
        -DBUILD_SHARED_LIBS=OFF -DGGML_CUDA=ON -DLLAMA_CURL=ON -DCMAKE_CUDA_ARCHITECTURES="$CMAKE_CUDA_ARCHITECTURES" && \
    cmake --build /tmp/llama.cpp/build --config Release -j --clean-first --target llama-quantize llama-cli llama-server llama-gguf-split && \
    cp /tmp/llama.cpp/build/bin/llama-* /opt/llama.cpp/bin && \
    rm -rf /tmp/llama.cpp && \
    mkdir -p /opt/workspace-internal/llama.cpp/models/ && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

    ENV PATH="${PATH}:/opt/llama.cpp/bin"
