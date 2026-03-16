ARG VLLM_OMNI_BASE=vllm/vllm-omni:v0.14.0
ARG VAST_BASE=vastai/base-image:stock-ubuntu24.04-py312

FROM ${VAST_BASE} AS vast_base_image
FROM ${VLLM_OMNI_BASE} AS vllm_omni_build

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="vLLM-Omni image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

### Convert non-Vast image to more closely resemble images derived from vastai/base-image ###
SHELL ["/bin/bash", "-c"]
ENV DATA_DIRECTORY=/workspace
ENV WORKSPACE=/workspace
ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV TCLLIBPATH=/usr/lib/tcltk/default
ENV PATH=/opt/instance-tools/bin:${PATH}
WORKDIR /

COPY --from=base_image_source /ROOT /
COPY --from=base_image_source /portal-aio /opt/portal-aio
COPY --from=vast_base_image /opt/portal-aio/caddy_manager/caddy /opt/portal-aio/caddy_manager/caddy
COPY --from=base_image_source tools/convert-non-vast-image.sh /tmp/convert-non-vast-image.sh

ARG TARGETARCH
RUN \
    set -euo pipefail && \
    chmod +x /tmp/convert-non-vast-image.sh && \
    /tmp/convert-non-vast-image.sh && \
    rm /tmp/convert-non-vast-image.sh

### Begin vLLM-Omni specific configuration ###

# Install flash-attention from pre-built wheel (matched to image Python/PyTorch/CUDA versions)
RUN \
    set -euo pipefail && \
    PYTHON_VER=$(python3 -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')") && \
    TORCH_VER=$(python3 -c "import torch; print('.'.join(torch.__version__.split('.')[:2]))") && \
    CXX11_ABI=$(python3 -c "import torch; print('TRUE' if torch._C._GLIBCXX_USE_CXX11_ABI else 'FALSE')") && \
    FA_VERSION=2.8.3 && \
    WHL="flash_attn-${FA_VERSION}+cu12torch${TORCH_VER}cxx11abi${CXX11_ABI}-${PYTHON_VER}-${PYTHON_VER}-linux_x86_64.whl" && \
    URL="https://github.com/Dao-AILab/flash-attention/releases/download/v${FA_VERSION}/${WHL}" && \
    echo "[flash-attn] Installing ${WHL}" && \
    uv pip install --system --no-cache-dir "${URL}"

# Install Model UI (lightweight web interface)
COPY --from=base_image_source tools/model-ui /opt/model-ui
RUN uv venv /opt/model-ui/venv && \
    uv pip install --python /opt/model-ui/venv/bin/python --no-cache-dir -r /opt/model-ui/requirements.txt

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Register Jupyter kernels (ipykernel installed into /venv/main by convert-non-vast-image.sh)
RUN /venv/main/bin/python -m ipykernel install --name "python3" --display-name "Python 3 (ipykernel)" && \
    /venv/main/bin/python -m ipykernel install --name "vllm-omni" --display-name "vLLM-Omni"

# Use familiar workspace
RUN \
    set -euo pipefail && \
    if [ -d /workspace ]; then mv /workspace /opt/workspace-internal; fi

# Add Ray dashboard
RUN \
    set -euo pipefail && \
    uv pip install --system --no-cache-dir ray[default]

ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
