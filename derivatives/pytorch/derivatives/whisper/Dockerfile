# torch 2.8.0, NOT the 2.7.1 its siblings use, and not a free choice. Whisper-WebUI
# v1.0.8 — the release this image builds — declares in requirements.txt
#
#     torchaudio>=2.8.0,<2.9.0
#
# and the strip below removes that line, because the pytorch base is the single
# source of truth for the torch ecosystem. So the pin was silently unsatisfied: the
# 2.7.1 base ships torchaudio 2.7.1, BELOW upstream's floor, and the drift-guard at
# the end of the RUN then dutifully asserted that nothing had changed. Stripping a
# constraint moves the obligation to this line; it does not discharge it.
#
# 2.8.0 satisfies the range exactly (torch-companions.json: 2.8.0 -> torchaudio
# 2.8.0). The UPPER bound is real too — upstream master carries
# "# 2.9 deprecates used functions" against it — so this is a fixed point, not a
# floor to raise later: do not move it to 2.9.x without re-reading upstream.
ARG PYTORCH_BASE=vastai/pytorch:2.8.0-cu128-cuda-12.9-mini-py312-2026-08-21

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Whisper-WebUI image (API + UI) suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG WHISPER_REPO=https://github.com/jhj0517/Whisper-WebUI
ARG WHISPER_REF

RUN \
    set -euo pipefail && \
    [[ -n "${WHISPER_REF}" ]] || { echo "Must specify WHISPER_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    # Clone Whisper-WebUI
    cd /opt/workspace-internal/ && \
    git clone "${WHISPER_REPO}" Whisper-WebUI && \
    cd Whisper-WebUI && \
    git checkout "${WHISPER_REF}" && \
    # Strip base-provided torch ecosystem pins from upstream requirements and
    # drop the --extra-index-url that points pip at the wrong torch wheel
    # index. The pytorch base is the single source of truth; the diff-based
    # assertion at the end of this RUN catches drift.
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])|^--extra-index-url.*pytorch/d' requirements.txt backend/requirements-backend.txt && \
    # Ensure setuptools<78 for legacy pkg_resources in build deps
    uv pip install --no-cache-dir 'setuptools<78' && \
    uv pip install --no-cache-dir --no-build-isolation -r requirements.txt -r backend/requirements-backend.txt && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
