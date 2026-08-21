ARG VLLM_BASE=vllm/vllm-openai:v0.13.0
ARG VAST_BASE=vastai/base-image:stock-ubuntu24.04-py312

FROM ${VAST_BASE} AS vast_base_image
FROM ${VLLM_BASE} AS vllm_build

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="vLLM image suitable for Vast.ai."
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
# uv hardlinks break volume sync; force copy (matches vastai/base-image)
ENV UV_LINK_MODE=copy
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

### Begin vLLM specific configuration ###

# Install Model UI (lightweight web interface) — chat only by default
ENV MODEL_UI_CHAT_CAPS=all
# Inference test endpoint: chat (default), none (skip).
# Override for non-chat models (e.g. embeddings, rerankers).
ENV VLLM_TEST_ENDPOINT=chat
COPY --from=base_image_source tools/model-ui /opt/model-ui
RUN uv venv /opt/model-ui/venv && \
    uv pip install --python /opt/model-ui/venv/bin/python --no-cache-dir -r /opt/model-ui/requirements.txt

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Register Jupyter kernels (ipykernel installed into /venv/main by convert-non-vast-image.sh)
RUN /venv/main/bin/python -m ipykernel install --name "python3" --display-name "Python 3 (ipykernel)" && \
    /venv/main/bin/python -m ipykernel install --name "vllm" --display-name "vLLM"

# Use familiar workspace
RUN \
    set -euo pipefail && \
    mv /vllm-workspace /opt/workspace-internal

# Add Ray dashboard
RUN \
    set -euo pipefail && \
    uv pip install --system --no-cache-dir ray[default]

# Fix vllm shebang so it runs through the venv Python, allowing users to
# install/upgrade dependencies (e.g. transformers) into /venv/main and have
# vllm pick them up.  Also enables volume sync of the environment.
RUN sed -i '1s|#!/usr/bin/python3|#!/venv/main/bin/python|' /usr/local/bin/vllm

# Serverless (autoscaler) PyWorker defaults, overridable by the launch template
# (docker -e / template env: wins over image ENV). No-op on-demand: pyworker.sh runs the
# worker only when SERVERLESS=true. BACKEND=openai selects the OpenAI-compatible PyWorker,
# which is the vLLM worker (openai is the alias for vllm in the per-engine split). openai
# also stays compatible with the current pyworker, so this image is safe to merge without
# waiting on the split. MODEL_LOAD_LOG_MSG is the log line the worker waits on to mark the
# model loaded: it can change between engine versions, and the image is the only layer
# version-locked to the engine build, so the image owns it.
ENV BACKEND=openai
ENV MODEL_LOAD_LOG_MSG="Application startup complete."

# Reserve the serverless worker port (WORKER_PORT default 3000). EXPOSE makes Vast map
# it, so VAST_TCP_PORT_3000 is set for the worker's self-report with NO template
# port entry. Only on these serverless images (not base-inherited); unbound and harmless
# on-demand (the worker binds 3000 only under SERVERLESS=true). 3000 stays reserved for the worker.
EXPOSE 3000

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash

ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
