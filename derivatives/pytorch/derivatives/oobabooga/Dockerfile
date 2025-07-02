ARG PYTORCH_BASE

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Oobabooga Text Generation WebUI image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG OOBABOOGA_REF

RUN \
    [[ -n "${OOBABOOGA_REF}" ]] || { echo "Must specify OOBABOOGA_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Install xformers while pinning to the inherited torch version.  Fail build on dependency resolution if matching version is unavailable
    pip install xformers torch==$PYTORCH_VERSION --index-url "${PYTORCH_INDEX_URL}" && \
    # Get A1111 and install dependencies (torch should not be pinned to a specific version in the requirements.txt - If it is then our build will probably fail)
    cd /opt/workspace-internal && \
    git clone https://github.com/oobabooga/text-generation-webui && \
    cd /opt/workspace-internal/text-generation-webui && \
    git checkout "${OOBABOOGA_REF}" && \
    cuda_int="$(echo "${CUDA_VERSION}" | cut -d. -f1,2 | tr -d '.')" && \
    if (( cuda_int >= 128 )); then \
        requirements_file=requirements/full/requirements.txt; \
    else \
        requirements_file=requirements/full/requirements_cuda128.txt; \
    fi && \
    uv pip install --no-cache-dir -r "$requirements_file" && \
    # Download custom models into the build if you want them - None included in default build
    # wget -O ${DATA_DIRECTORY}text-generation-webui/models/model https://url.to.files/model2 && \
    # wget -O ${DATA_DIRECTORY}text-generation-webui/models/Stable-diffusion/model2.safetensors https://url.to.files/model2 && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }
