ARG OLLAMA_BASE=ollama/ollama:0.17.1
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
ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV DEBIAN_FRONTEND=noninteractive
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

### Begin Ollama specific configuration ###

# SECURITY: the upstream ollama/ollama image bakes `ENV OLLAMA_HOST=0.0.0.0:11434`.
# Left in place, Ollama binds all interfaces on 11434 — colliding with the Caddy edge
# and, via the upstream `EXPOSE 11434`, exposing the API unauthenticated. Override the
# inherited value so Ollama binds loopback on the internal port; the Caddy edge proxies
# the external 11434 vhost to it with token auth. A Vast template env var still overrides
# this at runtime for anyone who deliberately wants a different bind.
ENV OLLAMA_HOST=127.0.0.1:21434

# Install Model UI (lightweight web interface) — chat only by default
ENV MODEL_UI_CHAT_CAPS=all
COPY --from=base_image_source tools/model-ui /opt/model-ui
RUN uv venv /opt/model-ui/venv && \
    uv pip install --python /opt/model-ui/venv/bin/python --no-cache-dir -r /opt/model-ui/requirements.txt

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Register Jupyter kernel (ipykernel installed into /venv/main by convert-non-vast-image.sh)
RUN /venv/main/bin/python -m ipykernel install --name "python3" --display-name "Python 3 (ipykernel)"

# Use familiar workspace (prevent not found error)
RUN \
    set -euo pipefail && \
    mkdir -p /opt/workspace-internal

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash

ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
