ARG PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312-2026-03-26

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Voicebox TTS image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG VOICEBOX_REF

RUN \
    set -euo pipefail && \
    [[ -n "${VOICEBOX_REF}" ]] || { echo "Must specify VOICEBOX_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print(torch.__version__)')" && \
    # System dependencies
    apt-get update && \
    apt-get install -y --no-install-recommends sox && \
    # Get Voicebox and checkout the specified ref
    cd /opt/ && \
    git clone https://github.com/jamiepine/voicebox.git && \
    cd /opt/voicebox && \
    git checkout "${VOICEBOX_REF}" && \
    # Install Python dependencies (strip torch pin — already installed)
    cd backend && \
    sed -i '/^torch>=/d' requirements.txt && \
    uv pip install --no-cache-dir https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3+cu12torch2.9cxx11abiTRUE-cp312-cp312-linux_x86_64.whl && \
    uv pip install --no-cache-dir -r requirements.txt && \
    uv pip install --no-cache-dir --no-deps chatterbox-tts && \
    uv pip install --no-cache-dir --no-deps hume-tada && \
    # Build web frontend (skip tsc — upstream has pre-existing type errors in shared app/ code)
    cd /opt/voicebox && \
    . /opt/nvm/nvm.sh && \
    npm install -g bun && \
    # Patch frontend to use same-origin instead of hardcoded localhost (for proxied deployments)
    sed -i "s#'http://localhost:17493'#window.location.origin#g" web/src/platform/lifecycle.ts && \
    sed -i "s#'http://127.0.0.1:17493'#window.location.origin#g" app/src/stores/serverStore.ts && \
    # Strip workspaces not needed for web build
    sed -i '/"tauri"/d; /"landing"/d' package.json && \
    sed -i -z 's/,\n  ]/\n  ]/' package.json && \
    bun install --no-save && \
    cd web && bunx --bun vite build && \
    cp -r dist ../frontend && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print(torch.__version__)')" && \
    [[ "$torch_version_pre" = "$torch_version_post" ]] || \
        { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; } && \
    # Cleanup
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV VOICEBOX_ARGS="--host 127.0.0.1 --port 17493"

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
