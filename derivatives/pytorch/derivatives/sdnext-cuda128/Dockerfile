ARG PYTORCH_BASE=vastai/pytorch:2.5.1-cuda-12.4.1-py311

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="SD.Next image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG SDNEXT_REF

RUN \
    set -euo pipefail && \
    [[ -n "${SDNEXT_REF}" ]] || { echo "Must specify SDNEXT_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    #torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    #SIKE!! We're gonna completely ignore the base image's PyTorch version and install our own
    # Don't need xformers bc it's not supported on current sdnext
    pip uninstall -y torch && \
    pip install --pre torch torchvision torchaudio --index-url "https://download.pytorch.org/whl/nightly/cu128" && \
    #keeping this to make sure that the requirements.txt doesn't do something stupid
    
    
    
    pip install onnxruntime-gpu insightface && \
    # Get Forge and install dependencies (torch should not be pinned to a specific version in the requirements.txt - If it is then our build will probably fail)
    cd /opt/workspace-internal/ && \
    git clone https://github.com/vladmandic/sdnext && \
    cd /opt/workspace-internal/sdnext && \
    git checkout "${SDNEXT_REF}" && \
    pip install --no-cache-dir \
        -r requirements.txt && \
    # Download custom models into the build if you want them - None included in default build
    # wget -O /opt/workspace-internal/sdnext/models/Stable-diffusion/model1.safetensors https://url.to.files/model1.safetensors && \
    # wget -O /opt/workspace-internal/sdnext/models/Stable-diffusion/model2.safetensors https://url.to.files/model2.safetensors && \
    # Quick startup test in CPU mode to ensure requirements ready and startup succeeds
    cd /opt/workspace-internal/sdnext && \
    LD_PRELOAD=libtcmalloc_minimal.so.4 \
        python launch.py \
            --quick \
            --port 11404 \
            --test && \
    
    # Test 1: Verify PyTorch version is unaltered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }
