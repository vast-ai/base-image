ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py310-2026-06-15

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="FluxGym image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Ensure uv pulls CUDA 12.8 wheels for torch ecosystem packages
ENV UV_TORCH_BACKEND=cu128

# Required or we will not build
ARG FLUXGYM_REPO=https://github.com/cocktailpeanut/fluxgym
ARG FLUXGYM_REF
ARG SD_SCRIPTS_REPO=https://github.com/kohya-ss/sd-scripts
ARG SD_SCRIPTS_REF=sd3

RUN \
    set -euo pipefail && \
    [[ -n "${FLUXGYM_REF}" ]] || { echo "Must specify FLUXGYM_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    # Clone FluxGym
    cd /opt/workspace-internal/ && \
    git clone "${FLUXGYM_REPO}" fluxgym && \
    cd fluxgym && \
    git checkout "${FLUXGYM_REF}" && \
    # Clone Kohya sd-scripts (sd3 branch) as a sibling tree inside fluxgym
    git clone -b "${SD_SCRIPTS_REF}" "${SD_SCRIPTS_REPO}" sd-scripts && \
    # Strip base-provided torch ecosystem pins from both upstream
    # requirements files so the pytorch base remains the single source of
    # truth. The diff-based assertion at the end of this RUN catches drift.
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' sd-scripts/requirements.txt requirements.txt && \
    # Install Kohya sd-scripts requirements first (fluxgym imports from it at runtime)
    (cd sd-scripts && uv pip install --no-cache-dir -r requirements.txt) && \
    # Install FluxGym requirements with pinned ecosystem versions.
    # transformers==4.49.0 and peft<0.18 are load-bearing (auto-caption and training regressions otherwise).
    # NOTE: don't re-list torch/torchvision/torchaudio here — the base
    # already provides them, and adding them to this install with
    # --torch-backend cu128 could trigger uv to re-resolve from the cu128
    # index and clobber the base.
    uv pip install --no-cache-dir \
        bitsandbytes \
        -r requirements.txt \
        'peft<0.18' \
        --torch-backend cu128 && \
    uv pip install --no-cache-dir transformers==4.49.0 && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
