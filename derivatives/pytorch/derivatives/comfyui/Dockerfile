ARG PYTORCH_BASE=vastai/pytorch:2..1-cuda-12.1.1

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="ComfyUI image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Move the base image entrypoint so we can use a custom launch
RUN \
    if [[ ! -f /opt/instance-tools/bin/entrypoint_base.sh ]]; then \
        mv /opt/instance-tools/bin/entrypoint.sh /opt/instance-tools/bin/entrypoint_base.sh; \
    fi

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG COMFYUI_REF

RUN \
    set -euo pipefail && \
    [[ -n "${COMFYUI_REF}" ]] || { echo "Must specify COMFYUI_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Fail build on dependency resolution if matching version is unavailable
    uv pip install --no-cache-dir torch==$PYTORCH_VERSION xformers sageattention==1.0.6 --extra-index-url "${PYTORCH_INDEX_URL}" && \
    # Get ComfyUI and install dependencies (torch should not be pinned to a specific version in the requirements.txt - If it is then our build will probably fail)
    cd /opt/workspace-internal/ && \
    git clone https://github.com/comfyanonymous/ComfyUI && \
    cd /opt/workspace-internal/ComfyUI && \
    git checkout "${COMFYUI_REF}" && \
    uv pip install --no-cache-dir \
        -r requirements.txt && \
    # Avoid Jupyter directory display bug
    cd /opt/workspace-internal/ComfyUI/models/ && \
    ln -s checkpoints ckpt && \
    # Install critical custom nodes
    cd /opt/workspace-internal/ComfyUI/custom_nodes && \
    git clone https://github.com/Comfy-Org/ComfyUI-Manager && \
    uv pip install --no-cache-dir -r ComfyUI-Manager/requirements.txt && \
    git clone https://github.com/cubiq/ComfyUI_essentials && \
    uv pip install --no-cache-dir -r ComfyUI_essentials/requirements.txt && \
    # Quick startup test in CPU mode to ensure requirements ready and startup succeeds
    cd /opt/workspace-internal/ComfyUI && \
    LD_PRELOAD=libtcmalloc_minimal.so.4 \
         python main.py \
            --cpu \
            --listen 127.0.0.1 \
            --port 11404 \
            --disable-auto-launch \
            --quick-test-for-ci && \
    mkdir -p /opt/workspace-internal/ComfyUI/user/default/ComfyUI-Manager/components && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

ENV COMFYUI_ARGS="--disable-auto-launch --enable-cors-header --port 18188"

# Install API wrapper
RUN \
    apt-get update && \
    apt-get install -y --no-install-recommends libmagic1 && \
    cd /opt && \
    git clone https://github.com/ai-dock/comfyui-api-wrapper && \
    cd comfyui-api-wrapper && \
    uv venv -p 3.12 && \
    . .venv/bin/activate && \
    uv pip install -r requirements.txt && \
    deactivate && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Generally we do not ship with models, but this is used for benchmarking the comfyui-json pyworker
# Always use a new RUN or COPY for each model added to keep layers to a reasonable size
RUN \
    mkdir /opt/model_store && \
    . /venv/main/bin/activate && \
    hf download Comfy-Org/stable-diffusion-v1-5-archive v1-5-pruned-emaonly-fp16.safetensors \
        --local-dir "/opt/model_store" && \
    # Symlink stored models to avoid moving large file to overlay/volume 
    ln -s /opt/model_store/v1-5-pruned-emaonly-fp16.safetensors \
        /opt/workspace-internal/ComfyUI/models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors && \
    rm -rf /opt/model_store/.cache && \
    rm -rf /root/.cache/huggingface

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
