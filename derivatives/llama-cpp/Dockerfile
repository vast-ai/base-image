ARG BASE_IMAGE=vastai/base-image:cuda-12.9-mini-py312

FROM ${BASE_IMAGE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Llama.cpp image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Install CUDA runtime libraries required by llama.cpp
RUN \
    set -euo pipefail && \
    CUDA_RUNTIME_VERSION="$(echo "${BASE_IMAGE}" | sed -n 's/.*cuda-\([0-9]\+\.[0-9]\+\).*/\1/p')" && \
    CUDA_MAJOR_MINOR="$(echo "${CUDA_RUNTIME_VERSION}" | tr '.' '-')" && \
    apt-get update && \
    apt-get install -y --no-install-recommends "libcublas-${CUDA_MAJOR_MINOR}" && \
    ldconfig && \
    rm -rf /var/lib/apt/lists/*

# Download and install llama.cpp-cuda binaries
ARG LLAMA_CPP_VERSION
ARG CUDA_VERSION=12.8
ENV CUDA_VERSION=${CUDA_VERSION}
RUN \
    set -euo pipefail && \
    [[ -n "${LLAMA_CPP_VERSION}" ]] || { echo "Must specify LLAMA_CPP_VERSION"; exit 1; } && \
    mkdir -p /opt/llama.cpp && \
    package_name="llama.cpp-${LLAMA_CPP_VERSION}-cuda-${CUDA_VERSION}.tar.gz" && \
    download_url="https://github.com/ai-dock/llama.cpp-cuda/releases/download/${LLAMA_CPP_VERSION}/${package_name}" && \
    wget -O "/opt/llama.cpp/${package_name}" "${download_url}" && \
    cd /opt/llama.cpp && tar xf "${package_name}" && \
    rm "/opt/llama.cpp/${package_name}"

# Add llama.cpp binaries to PATH and library search path
ENV LLAMA_CPP_DIR=/opt/llama.cpp/cuda-${CUDA_VERSION}
ENV PATH=${LLAMA_CPP_DIR}:${PATH}

