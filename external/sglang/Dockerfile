ARG SGLANG_BASE=lmsysorg/sglang:v0.5.8
ARG VAST_BASE=vastai/base-image:stock-ubuntu24.04-py312

FROM ${VAST_BASE} AS vast_base_image
FROM ${SGLANG_BASE} AS sglang_build

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="SGLang image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

### Convert non-Vast image to more closely resemble images derived from vastai/base-image ###
SHELL ["/bin/bash", "-c"]
ENV DATA_DIRECTORY=/workspace
ENV WORKSPACE=/workspace
ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV TCLLIBPATH=/usr/lib/tcltk/default
ENV PATH=/opt/instance-tools/bin:/opt/sys-venv/shim:${PATH}
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

### Begin SGLang specific configuration ###

# Images generally work via minor version compatibility for CUDA 12.0+
# Templates should set proper constraints on a per-model/cc/version basis
ARG CUDA_MAJOR_VERSION=12
ENV NVIDIA_REQUIRE_CUDA="cuda>=${CUDA_MAJOR_VERSION}.0"

RUN \
    set -euo pipefail && \
    # This base image sets unusual defaults
    rm -f /root/.tmux.conf

# Install Model UI (lightweight web interface) — chat only by default
ENV MODEL_UI_CHAT_CAPS=all
COPY --from=base_image_source tools/model-ui /opt/model-ui
RUN uv venv /opt/model-ui/venv && \
    uv pip install --python /opt/model-ui/venv/bin/python --no-cache-dir -r /opt/model-ui/requirements.txt

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Register Jupyter kernels (ipykernel installed into /venv/main by convert-non-vast-image.sh)
RUN /venv/main/bin/python -m ipykernel install --name "python3" --display-name "Python 3 (ipykernel)" && \
    /venv/main/bin/python -m ipykernel install --name "sglang" --display-name "SGLang"

# Use familiar workspace. Do not move the real sgl-workspace
RUN \
    set -euo pipefail && \
    mkdir /opt/workspace-internal && \
    ln -s /sgl-workspace /opt/workspace-internal/sgl-workspace-link

# Fix sglang shebang so it runs through the venv Python, allowing users to
# install/upgrade dependencies (e.g. transformers) into /venv/main and have
# vllm pick them up.  Also enables volume sync of the environment.
RUN sed -i '1s|#!/usr/bin/python3|#!/venv/main/bin/python|' /usr/local/bin/sglang

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash

ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
