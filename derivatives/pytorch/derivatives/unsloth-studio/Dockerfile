ARG PYTORCH_BASE=vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-03-26
FROM ${PYTORCH_BASE}

LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Unsloth Studio image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

COPY ./ROOT /

# Install CUDA runtime libraries required by llama.cpp build
ENV UV_TORCH_BACKEND=cu128
ARG PYTORCH_BASE
RUN \
    set -euo pipefail && \
    apt-get update && \
    apt-get install -y --no-install-recommends "libcublas-dev-12-9" && \
    ldconfig && \
    rm -rf /var/lib/apt/lists/* && \
    # Make CUDA driver stubs available for linking at build time (no GPU in Docker build)
    echo "/usr/local/cuda/lib64/stubs" > /etc/ld.so.conf.d/cuda-stubs.conf && \
    ldconfig

# Install unsloth, symlink the studio venv to /venv/main so setup.sh installs
# everything into our existing venv (avoids a duplicate torch install), then
# run studio setup for frontend build + llama.cpp + all python deps.
RUN \
    set -euo pipefail && \
    . /opt/nvm/nvm.sh && \
    . /venv/main/bin/activate && \
    torch_version_pre="$(python -c 'import torch; print(torch.__version__)')" && \
    uv pip install --no-cache-dir --torch-backend=cu128 unsloth && \
    # Store unsloth data under /opt so it survives volume mounts over /root
    mkdir -p /opt/workspace-internal/unsloth/studio && \
    ln -sf /opt/workspace-internal/unsloth /root/.unsloth && \
    # Symlink studio venv → /venv/main so setup.sh finds an existing venv
    ln -sf /venv/main /opt/workspace-internal/unsloth/studio/unsloth_studio && \
    # Patch install_python_stack.py to use cu128 torch backend instead of auto-detection
    find /venv/main/lib -name 'install_python_stack.py' -path '*/studio/*' \
        -exec sed -i 's|--torch-backend=auto|--torch-backend=cu128|' {} + && \
    # Skip prebuilt llama.cpp (needs nvidia-smi), go straight to source build
    export UNSLOTH_LLAMA_FORCE_COMPILE=1 && \
    export SKIP_STUDIO_BASE=1 && \
    unsloth studio setup && \
    # Add llama.cpp shared libs to linker search path
    echo "/opt/workspace-internal/unsloth/llama.cpp/build/bin" > /etc/ld.so.conf.d/llama-cpp.conf && \
    ldconfig && \
    # Verify PyTorch version not clobbered
    torch_version_post="$(python -c 'import torch; print(torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
