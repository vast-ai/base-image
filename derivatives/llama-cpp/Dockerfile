ARG BASE_IMAGE=vastai/base-image:cuda-12.9-mini-py312-2026-08-21

FROM ${BASE_IMAGE}
ARG BASE_IMAGE

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Llama.cpp image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Install CUDA runtime libraries required by llama.cpp
ARG BASE_IMAGE
RUN \
    set -euo pipefail && \
    CUDA_RUNTIME_VERSION="$(echo "${BASE_IMAGE}" | sed -n 's/.*cuda-\([0-9]\+\.[0-9]\+\).*/\1/p')" && \
    CUDA_MAJOR_MINOR="$(echo "${CUDA_RUNTIME_VERSION}" | tr '.' '-')" && \
    apt-get update && \
    apt-get install -y --no-install-recommends "libcublas-${CUDA_MAJOR_MINOR}" && \
    ldconfig && \
    rm -rf /var/lib/apt/lists/*

# Download and install llama.cpp-cuda binaries.
# Upstream `ai-dock/llama.cpp-cuda` publishes one tarball per host CPU arch:
#   llama.cpp-<ver>-cuda-<cuda>-amd64.tar.gz  (x86_64 hosts)
#   llama.cpp-<ver>-cuda-<cuda>-arm64.tar.gz  (aarch64 hosts — Grace Hopper / Grace Blackwell / DGX Spark)
# Both contain the same CUDA-compute-capability set; only the host CPU
# binaries differ. Pick by `dpkg --print-architecture`.
ARG LLAMA_CPP_VERSION
ARG CUDA_VERSION=12.8
ENV CUDA_VERSION=${CUDA_VERSION}
RUN \
    set -euo pipefail && \
    [[ -n "${LLAMA_CPP_VERSION}" ]] || { echo "Must specify LLAMA_CPP_VERSION"; exit 1; } && \
    ARCH="$(dpkg --print-architecture)" && \
    mkdir -p /opt/llama.cpp && \
    package_name="llama.cpp-${LLAMA_CPP_VERSION}-cuda-${CUDA_VERSION}-${ARCH}.tar.gz" && \
    download_url="https://github.com/ai-dock/llama.cpp-cuda/releases/download/${LLAMA_CPP_VERSION}/${package_name}" && \
    wget -O "/opt/llama.cpp/${package_name}" "${download_url}" && \
    cd /opt/llama.cpp && tar xf "${package_name}" && \
    rm "/opt/llama.cpp/${package_name}"

# Add llama.cpp binaries to PATH and library search path
ENV LLAMA_CPP_DIR=/opt/llama.cpp/cuda-${CUDA_VERSION}
ENV PATH=${LLAMA_CPP_DIR}:${PATH}

# Serverless (autoscaler) PyWorker defaults, overridable by the launch template
# (docker -e / template env: wins over image ENV). No-op on-demand: pyworker.sh runs the
# worker only when SERVERLESS=true. BACKEND=llama selects the llama.cpp PyWorker
# (workers/llama, a thin adapter over the shared OpenAI-compatible core). MODEL_LOAD_LOG_MSG
# is the log line the worker waits for to mark the model loaded: llama-server prints
# "model loaded" AFTER /health flips 503->200, so it is a safe marker (unlike "listening on",
# which prints while /health still returns 503). It is version-specific, so the image — the
# only layer version-locked to the engine — owns it.
ENV BACKEND=llama
ENV MODEL_LOAD_LOG_MSG="model loaded"

# Reserve the serverless worker port (WORKER_PORT default 3000). EXPOSE makes Vast map
# it, so VAST_TCP_PORT_3000 is set for the worker's self-report with NO template
# port entry. Only on these serverless images (not base-inherited); unbound and harmless
# on-demand (the worker binds 3000 only under SERVERLESS=true). 3000 stays reserved for the worker.
EXPOSE 3000

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash

