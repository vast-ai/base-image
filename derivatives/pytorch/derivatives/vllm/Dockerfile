ARG PYTORCH_BASE=vastai/pytorch:2.7.0-cuda-12.6.3-py312

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="vLLM image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG VLLM_REF

RUN \
    [[ -n "${VLLM_REF}" ]] || { echo "Must specify VLLM_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Install xformers while pinning to the inherited torch version.  Fail build on dependency resolution if matching version is unavailable
    uv pip install --no-cache-dir xformers torch==$PYTORCH_VERSION --index-url "${PYTORCH_INDEX_URL}" && \
    if [[ "${VLLM_REF},,}" = "nightly" ]]; then \
      uv pip install vllm --extra-index-url https://wheels.vllm.ai/nightly; \
    else \
      uv pip install vllm=="${VLLM_REF}"; \
    fi && \
    cuda_int="$(echo "${CUDA_version}" | cut -d. -f1,2 | tr -d '.')" && \
    if (( cuda_int >= 128 )); then \
        uv pip install flashinfer-python --extra-index-url "${PYTORCH_INDEX_URL}"; \
    else \
        uv pip install flashinfer-python; \
    fi && \
    uv pip install ray[default] && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }
