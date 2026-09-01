ARG BASE_IMAGE=vastai/base-image:cuda-12.9-mini-py312-2026-08-28

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

# Download and install llama.cpp binaries from `unslothai/llama.cpp` (ADR 0033).
#
# Not ai-dock, and the reason is the catalogue rather than the build: the fork carries
# architectures and quant types upstream has not merged (DiffusionGemma, TML Inkling,
# kimi-k3's vision tower, IQ1_XS/XXS/XXXS), and the model library publishes GGUFs for
# exactly those. On stock llama.cpp — including ai-dock's build — those models do not
# load at all.
#
# Asset naming: app-<ref>-linux-<archslug>-<bundle>.tar.gz. The arch slug is unsloth's,
# NOT dpkg's — amd64 is spelled `x64` there — so it is mapped rather than passed
# through. The bundle is chosen per CUDA line by the CI matrix, because there is no
# arm64 CUDA 12 asset at all and the lines therefore differ in shape, not just in
# version (ADR 0033: cuda-12.9 is amd64-only, cuda-13.2 is multi-arch).
#
# Layout is FLAT — no cuda-<ver>/ subdirectory like ai-dock's — with RUNPATH=$ORIGIN,
# so the bundle resolves its own libggml-* siblings with no loader configuration.
# libcublas/libcudart still come from the system, and the base-derived package is
# sufficient: measured, the cuda13 bundle needs 12 cuBLAS and 49 cudart symbols and
# every one is exported by the 13.2 packages, so a bundle built against toolkit 13.3
# resolves cleanly (ADR 0033 condition 9).
ARG LLAMA_CPP_VERSION
ARG LLAMA_BUNDLE=cuda12-portable
ENV LLAMA_BUNDLE=${LLAMA_BUNDLE}
RUN \
    set -euo pipefail && \
    [[ -n "${LLAMA_CPP_VERSION}" ]] || { echo "Must specify LLAMA_CPP_VERSION"; exit 1; } && \
    case "$(dpkg --print-architecture)" in \
        amd64) arch_slug=x64 ;; \
        arm64) arch_slug=arm64 ;; \
        *) echo "unsupported build arch: $(dpkg --print-architecture)"; exit 1 ;; \
    esac && \
    package_name="app-${LLAMA_CPP_VERSION}-linux-${arch_slug}-${LLAMA_BUNDLE}.tar.gz" && \
    base_url="https://github.com/unslothai/llama.cpp/releases/download/${LLAMA_CPP_VERSION}" && \
    mkdir -p /opt/llama.cpp /tmp/llama-dl && \
    wget -O "/tmp/llama-dl/${package_name}" "${base_url}/${package_name}" && \
    wget -O /tmp/llama-dl/sha256.json "${base_url}/llama-prebuilt-sha256.json" && \
    # VERIFY BEFORE EXTRACT, and fail when the manifest has no entry for OUR asset
    # rather than comparing empty-to-empty — the sha256 of no input is a constant, so
    # two failed lookups compare equal and the check passes having verified nothing.
    # This is the first integrity check on any third-party fetch in this repo; be
    # honest about its reach: the hashes are published by the same party as the asset,
    # so it detects transport damage, not a compromised publishing path.
    python3 -c "\
import json,hashlib,sys;\
name=sys.argv[1];\
m=json.load(open('/tmp/llama-dl/sha256.json')).get('artifacts',{});\
e=m.get(name) or sys.exit('no sha256 entry for '+name+' in llama-prebuilt-sha256.json');\
want=e.get('sha256') or sys.exit('empty sha256 entry for '+name);\
h=hashlib.sha256(open('/tmp/llama-dl/'+name,'rb').read()).hexdigest();\
sys.exit(0) if h==want else sys.exit('sha256 mismatch for '+name+': got '+h+' want '+want)\
" "${package_name}" && \
    tar -xf "/tmp/llama-dl/${package_name}" -C /opt/llama.cpp && \
    rm -rf /tmp/llama-dl && \
    # The bundle states its own provenance — supported SMs, toolkit, and the merged_prs
    # list that is the whole reason for the fork. Keep it: nothing else in the image can
    # say which llama.cpp it carries, and `merged_prs` is publisher-DECLARED, so it is
    # evidence of intent rather than of content.
    test -f /opt/llama.cpp/UNSLOTH_PREBUILT_INFO.json && \
    test -x /opt/llama.cpp/llama-server && \
    test -f /opt/llama.cpp/libggml-cuda.so

# Flat layout: the binaries and their libraries sit together and RUNPATH=$ORIGIN
# resolves them, so there is no ld.so.conf entry to add. `06-llama-cuda.sh` existed
# solely to re-add /opt/llama.cpp/cuda-<ver> after 05-configure-cuda.sh strips every
# line containing "cuda" from every conf file on every boot; that directory no longer
# exists and the file is deleted with this change.
ENV LLAMA_CPP_DIR=/opt/llama.cpp
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

