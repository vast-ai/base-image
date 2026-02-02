ARG PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py311

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Stable Diffusion Forge image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

RUN apt-get update && apt-get install -y \
    pkg-config \
    libcairo2-dev \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Required or we will not build
ARG FORGE_REPO
ARG FORGE_REF

RUN \
    set -euo pipefail && \
    [[ -n "${FORGE_REPO}" && -n "${FORGE_REF}" ]] || { echo "Must specify FORGE_REPO and FORGE_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Install xformers while pinning to the inherited torch version.  Fail build on dependency resolution if matching version is unavailable
    uv pip install --no-cache-dir xformers torch==$PYTORCH_VERSION --torch-backend "${PYTORCH_BACKEND}" && \
    uv pip install --no-cache-dir onnxruntime-gpu insightface && \
    # Get Forge and install dependencies (torch should not be pinned to a specific version in the requirements.txt - If it is then our build will probably fail)
    cd /opt/workspace-internal/ && \
    git clone "${FORGE_REPO}" /opt/workspace-internal/stable-diffusion-webui-forge && \
    cd /opt/workspace-internal/stable-diffusion-webui-forge && \
    git checkout "${FORGE_REF}" && \
    # Patch neo's interactive version check
    sed -i 's/    launch_utils.verify_version()/#    launch_utils.verify_version()  # bypassed for Docker/'  launch.py && \
    uv pip install --no-cache-dir \
        -r $([[ -f requirements_versions.txt ]] && echo "requirements_versions.txt" || echo "requirements.txt") && \
    # Download custom models into the build if you want them - None included in default build
    # wget -O /opt/workspace-internal/stable-diffusion-webui-forge/models/Stable-diffusion/model1.safetensors https://url.to.files/model1.safetensors && \
    # wget -O /opt/workspace-internal/stable-diffusion-webui-forge/models/Stable-diffusion/model2.safetensors https://url.to.files/model2.safetensors && \
    # Quick startup test in CPU mode to ensure requirements ready and startup succeeds
    cd /opt/workspace-internal/stable-diffusion-webui-forge && \
    LD_PRELOAD=libtcmalloc_minimal.so.4 \
        python launch.py \
            --use-cpu all \
            --skip-torch-cuda-test \
            --skip-python-version-check \
            --no-download-sd-model \
            --do-not-download-clip \
            --no-half \
            --port 11404 \
            --exit && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }
