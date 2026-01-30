ARG OLLAMA_BASE=ollama/ollama:0.15.2
ARG VAST_BASE=vastai/base-image:stock-ubuntu24.04-py312

FROM ${VAST_BASE} AS vast_base_image
FROM ${OLLAMA_BASE} AS ollama_build

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Ollama image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

### Convert non-Vast image to more closely resemble images derived from vastai/base-image ###
SHELL ["/bin/bash", "-c"]
ENV DATA_DIRECTORY=/workspace
ENV WORKSPACE=/workspace
ENV DEBIAN_FRONTEND=noninteractive
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

### Begin Ollama specific configuration ###

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Ensure we have a Jupyter kernel for Ollamas system python environment
RUN \
    uv pip install --system --break-system-packages ipykernel && \
    python3 -m ipykernel install --name "python3" --display-name "Python 3 (ipykernel)"

ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
