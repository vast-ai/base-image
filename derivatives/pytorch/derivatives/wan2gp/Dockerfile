ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312-2026-04-15

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Wan2GP image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG WAN2GP_REPO=https://github.com/deepbeepmeep/Wan2GP
ARG WAN2GP_REF

RUN \
    set -euo pipefail && \
    [[ -n "${WAN2GP_REF}" ]] || { echo "Must specify WAN2GP_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    # Combined with the strip below + the diff-based assertion at the end,
    # this catches silent ABI clobbers — the torch-only check we used to
    # do here let them slip through.
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    # Wan2GP needs ALSA for SDL audio even in dummy mode
    apt-get update && apt-get install --no-install-recommends -y libasound2-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    # Clone and install Wan2GP
    cd /opt/workspace-internal/ && \
    git clone "${WAN2GP_REPO}" Wan2GP && \
    cd Wan2GP && \
    git checkout "${WAN2GP_REF}" && \
    # Strip all base-provided torch ecosystem pins from upstream requirements
    # so the pytorch base remains the single source of truth. Wan2GP pins
    # torchcodec==0.10.0 but never imports torchcodec anywhere in its source
    # (dead dep). More generally: torchcodec's PyPI metadata declares no
    # torch requirement, and uv won't catch a torch/torch-ecosystem version
    # skew via dependency resolution — the diff-based assertion below is
    # what catches it now. Strip these four uniformly across all
    # pytorch-base derivatives.
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' requirements.txt && \
    uv pip install --no-cache-dir -r requirements.txt && \
    # Verify the torch ecosystem (torch + torchvision + torchaudio + torchcodec)
    # versions are unchanged. Catches silent ABI clobbers that the
    # torch-only check used to miss.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
