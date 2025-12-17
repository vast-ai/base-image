ARG VLLM_BASE=vllm/vllm-openai:v0.12.0
ARG VAST_BASE=vastai/base-image:stock-ubuntu24.04-py312

FROM ${VAST_BASE} AS vast_base_image
FROM ${VLLM_BASE} AS vllm_build

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="vLLM image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

### Convert non-Vast image to more closely resemble images derrived from vastai/base-image ###
SHELL ["/bin/bash", "-c"]
ENV DATA_DIRECTORY=/workspace
ENV WORKSPACE=/workspace
ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
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

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Use familiar workspace
RUN \
    set -euo pipefail && \
    mv /vllm-workspace /opt/workspace-internal

# Add Ray dashboard
RUN \
    set -euo pipefail && \
    uv pip install --system --no-cache-dir ray[default]

ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
