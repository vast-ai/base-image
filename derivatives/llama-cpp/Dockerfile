ARG BASE_IMAGE=vastai/base-image:cuda-12.9-mini-py312-2026-06-15

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
    apt-get install -y --no-install-recommends "libcublas-${CUDA_MAJOR_MINOR}" "cuda-cudart-${CUDA_MAJOR_MINOR}" && \
    ldconfig && \
    rm -rf /var/lib/apt/lists/*

# Download and install llama.cpp-cuda binaries.
# ONE-OFF BUILD — NOT the standard sourcing for this image.
#
# llama.cpp here comes from the Unsloth fork's prebuilt CUDA bundle
# (`unslothai/llama.cpp`) instead of `ai-dock/llama.cpp-cuda`, because the model this
# build exists to serve is supported only by that fork's merged patches today. The fork
# publishes "mix" builds carrying pull requests that have not passed upstream review —
# acceptable for a targeted build, which is why this is not on main.
#
# Two deliberate consequences:
#   - amd64 only. The fork's sole arm64 bundle is CUDA 13 / sm_90+, which does not fit
#     this CUDA 12 image line; ai-dock covered both arches from one arch list.
#   - No arch regression: the bundle carries SASS for sm_70-120 plus PTX (verified with
#     `cuobjdump` on the artifact), where ai-dock builds PTX-only for sm_75-120. Every
#     GPU ai-dock covered is still covered, and common arches now skip the first-load
#     JIT entirely.
#
# Release tag, exact asset name and sha256 are resolved in CI (resolve-llama-bundle) and
# verified here; this layer fetches one known URL and checks one known hash.
ARG LLAMA_RELEASE=
ARG LLAMA_ASSET=
ARG LLAMA_SHA256=
ARG CUDA_VERSION=12.8
ENV CUDA_VERSION=${CUDA_VERSION}
RUN \
    set -euo pipefail && \
    for _v in LLAMA_RELEASE LLAMA_ASSET LLAMA_SHA256; do \
        [[ -n "${!_v:-}" ]] || { echo "FATAL: build arg ${_v} is required (resolved by CI)"; exit 1; }; \
    done && \
    _dir="/opt/llama.cpp/cuda-${CUDA_VERSION}" && \
    _tmp="$(mktemp -d)" && \
    curl -fsSL --retry 5 --retry-delay 2 --retry-all-errors --max-time 900 \
        -o "${_tmp}/${LLAMA_ASSET}" \
        "https://github.com/unslothai/llama.cpp/releases/download/${LLAMA_RELEASE}/${LLAMA_ASSET}" && \
    echo "${LLAMA_SHA256}  ${_tmp}/${LLAMA_ASSET}" | sha256sum -c - && \
    mkdir -p "${_dir}" && \
    # Extract with Python's `data` filter rather than `tar xf`: it refuses absolute paths,
    # `..` traversal, links pointing outside the destination, device nodes and setuid bits.
    # The bundle is flat, so it lands directly in the directory LLAMA_CPP_DIR already names.
    python3 -c "\
import sys, tarfile;\
src, dest = sys.argv[1], sys.argv[2];\
tarfile.open(src).extractall(dest, filter='data')" "${_tmp}/${LLAMA_ASSET}" "${_dir}" && \
    rm -rf "${_tmp}" && \
    # The binary must exist AND execute: the fork links llama-server against OpenSSL,
    # which the ai-dock build did not, so a present-but-unloadable binary is possible.
    test -x "${_dir}/llama-server" || \
        { echo "FATAL: bundle ${LLAMA_ASSET} contains no llama-server"; exit 1; } && \
    "${_dir}/llama-server" --version || \
        { echo "FATAL: llama-server does not execute — its link closure is incomplete"; exit 1; } && \
    # llama.cpp is built with GGML_BACKEND_DL: a CUDA backend whose NEEDED libs are absent
    # is SKIPPED at dlopen and every inference silently runs on CPU. libcuda.so.1 is the
    # driver, injected by the container runtime, so it is the only permitted miss here.
    test -f "${_dir}/libggml-cuda.so" || \
        { echo "FATAL: bundle has no CUDA backend (libggml-cuda.so absent)"; exit 1; } && \
    _ldd="$(ldd -r "${_dir}/libggml-cuda.so" 2>&1 || true)" && \
    _missing="$(printf '%s\n' "$_ldd" | grep 'not found' | grep -v 'libcuda\.so\.1' || true)" && \
    { [[ -z "$_missing" ]] || \
        { echo "FATAL: libggml-cuda.so has unresolved libraries: $_missing"; exit 1; }; } && \
    # MIT requires the notice to accompany binary redistribution.
    test -f "${_dir}/THIRD_PARTY_LICENSES.txt" || \
        { echo "FATAL: bundle ships no third-party licence notice"; exit 1; }

# Record which llama.cpp is baked in, readable without booting the image.
ENV LLAMA_CPP_RELEASE=${LLAMA_RELEASE}

# Add llama.cpp binaries to PATH and library search path
ENV LLAMA_CPP_DIR=/opt/llama.cpp/cuda-${CUDA_VERSION}
ENV PATH=${LLAMA_CPP_DIR}:${PATH}

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash

