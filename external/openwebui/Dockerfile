ARG OPENWEBUI_BASE=ghcr.io/open-webui/open-webui:v0.8.5-cuda
ARG VAST_BASE=vastai/base-image:stock-ubuntu24.04-py312
ARG OLLAMA_VERSION=0.15.2

FROM ${VAST_BASE} AS vast_base_image
FROM ${OPENWEBUI_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Open WebUI + Ollama image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

### Convert non-Vast image to more closely resemble images derived from vastai/base-image ###
SHELL ["/bin/bash", "-c"]
ENV DATA_DIRECTORY=/workspace
ENV WORKSPACE=/workspace
ENV DEBIAN_FRONTEND=noninteractive
ENV PATH=/opt/sys-venv/shim:/opt/instance-tools/bin:${PATH}
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

### Begin Open WebUI + Ollama specific configuration ###

# Override upstream default (http://host.docker.internal:11434) — Ollama is local on 21434
ENV OLLAMA_BASE_URL=http://localhost:21434

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Install Ollama from GitHub releases
ARG OLLAMA_VERSION
RUN \
    set -euo pipefail && \
    curl -fsSL "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-${TARGETARCH:-amd64}.tar.zst" \
        -o /tmp/ollama.tar.zst && \
    tar --use-compress-program=unzstd -xf /tmp/ollama.tar.zst -C /usr && \
    rm /tmp/ollama.tar.zst && \
    ollama --version

# Seed persistent data directories (synced to workspace at boot via 36-sync-workspace.sh)
# Move upstream Open WebUI database & data into workspace-internal so it lands in $WORKSPACE/data
RUN mkdir -p /opt/workspace-internal/ollama && \
    mv /app/backend/data /opt/workspace-internal/data

# Ensure we have a Jupyter kernel for the image's own Python
RUN \
    uv pip install --python /usr/local/bin/python3 --break-system-packages ipykernel && \
    /usr/local/bin/python3 -m ipykernel install --name "python3" --display-name "Python 3 (ipykernel)"

ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
