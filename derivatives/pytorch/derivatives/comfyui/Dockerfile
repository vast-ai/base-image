ARG PYTORCH_BASE=vastai/pytorch:2.5.1-cuda-12.1.1

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="ComfyUI image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

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
    # Install xformers while pinning to the inherited torch version.  Fail build on dependency resolution if matching version is unavailable
    uv pip install torch==$PYTORCH_VERSION --index-url "${PYTORCH_INDEX_URL}" && \
    # Get ComfyUI and install dependencies (torch should not be pinned to a specific version in the requirements.txt - If it is then our build will probably fail)
    cd /opt/workspace-internal/ && \
    git clone https://github.com/comfyanonymous/ComfyUI && \
    cd /opt/workspace-internal/ComfyUI && \
    git checkout "${COMFYUI_REF}" && \
    uv pip install --no-cache-dir \
        -r requirements.txt && \
    # Avoid Jupyter directory display bug
    ln -s /opt/workspace-internal/ComfyUI/models/checkpoints /opt/workspace-internal/ComfyUI/models/ckpt && \
    # Install critical custom nodes
    cd /opt/workspace-internal/ComfyUI/custom_nodes && \
    git clone https://github.com/ltdrdata/ComfyUI-Manager && \
    uv pip install -r ComfyUI-Manager/requirements.txt && \
    git clone https://github.com/cubiq/ComfyUI_essentials && \
    uv pip install -r ComfyUI_essentials/requirements.txt && \
    # Download custom models into the build if you want them - None included in default build
    # wget -O /opt/workspace-internal/ComfyUI/models/checkpoints/model1.safetensors https://url.to.files/model1.safetensors && \
    # wget -O /opt/workspace-internal/ComfyUI/models/checkpoints/model2.safetensors https://url.to.files/model2.safetensors && \
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

    ENV COMFYUI_ARGS="--disable-auto-launch --port 18188"