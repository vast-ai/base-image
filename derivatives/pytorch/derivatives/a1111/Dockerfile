ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py310-2026-03-26

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Stable Diffusion A1111 image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Pin setuptools<78 — legacy packages (e.g. CLIP) use pkg_resources which was
# removed in setuptools 78+. Persists into runtime for the A1111 extension manager.
RUN echo 'setuptools<78' > /etc/pip-constraint.txt
ENV PIP_CONSTRAINT=/etc/pip-constraint.txt

# Required or we will not build
ARG A1111_REF=dev

RUN \
    set -euo pipefail && \
    [[ -n "${A1111_REF}" ]] || { echo "Must specify A1111_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Install xformers while pinning to the inherited torch version.  Fail build on dependency resolution if matching version is unavailable
    uv pip install --no-cache-dir xformers torch=="${PYTORCH_VERSION}" --torch-backend "${PYTORCH_BACKEND}" && \
    uv pip install --no-cache-dir onnxruntime-gpu && \
    # Get A1111 and install dependencies (torch should not be pinned to a specific version in the requirements.txt - If it is then our build will probably fail)
    cd /opt/workspace-internal/ && \
    git clone --recursive https://github.com/AUTOMATIC1111/stable-diffusion-webui && \
    cd /opt/workspace-internal/stable-diffusion-webui && \
    git checkout "${A1111_REF}" && \
    uv pip install --no-cache-dir \
        -r requirements_versions.txt && \
    # Download custom models into the build if you want them - None included in default build
    # wget -O /opt/workspace-internal/stable-diffusion-webui/models/Stable-diffusion/model1.safetensors https://url.to.files/model1.safetensors && \
    # wget -O /opt/workspace-internal/stable-diffusion-webui/models/Stable-diffusion/model2.safetensors https://url.to.files/model2.safetensors && \
    # Quick startup test in CPU mode to ensure requirements ready and startup succeeds
    cd /opt/workspace-internal/stable-diffusion-webui && \
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

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
