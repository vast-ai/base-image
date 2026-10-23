# torch 2.7.1, KNOWINGLY below upstream's floor. Whisper-WebUI v1.0.8 -- the release this
# image builds -- declares in requirements.txt
#
#     torchaudio>=2.8.0,<2.9.0
#
# and the strip below removes that line, because the pytorch base is the single source of
# truth for the torch ecosystem. Stripping a constraint moves the obligation to this line; it
# does not discharge it, and the drift guard at the end of the RUN cannot see it.
#
# 2.8.0 satisfies the range exactly (torch-companions.json: 2.8.0 -> torchaudio 2.8.0), but the
# 2.8.0 mini base is amd64-only, so whisper on it cannot ship arm64. This image stays on 2.7.1
# for both arches: the same torch the published 1.0.8 has run on since June, and the rebuild on
# it was checked live (transcription of uploaded files; the build itself imports uvr and decodes
# audio through faster-whisper). Move to 2.8.x once a 2.8.x mini base ships arm64; the upper
# bound is real ("# 2.9 deprecates used functions" upstream), so never 2.9.x without re-reading
# upstream.
ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312-2026-10-23

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
    # faster-whisper 1.1.1 (pinned upstream) calls av.open(..., metadata_errors=...), which PyAV 19 removed,
    # and it does not cap av: without this every uploaded file fails to open.
    uv pip install --no-cache-dir --no-build-isolation -r requirements.txt -r backend/requirements-backend.txt 'av<19' && \
    # uvr imports audioread without declaring it; librosa supplied it until 1.0 dropped the dependency,
    # and without it BGM separation fails to import and the API refuses to start.
    uv pip install --no-cache-dir audioread && \
    /venv/main/bin/python -c "from uvr.models import MDX, Demucs, VrNetwork, MDXC" && \
    /venv/main/bin/python -c "import tempfile, wave, faster_whisper.audio as fa; f = tempfile.NamedTemporaryFile(suffix='.wav', delete=False); w = wave.open(f.name, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(3200)); w.close(); fa.decode_audio(f.name)" && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# pytubefix depends on nodejs-wheel-binaries, which bundles its own Node (24.19.0 at the time of
# writing, below the Node 24 trust floor, with no fixed release on PyPI). Node compiles its roots in,
# so point the bundled binary at the image's own Node of the same major line. One-off until upstream
# ships a fixed wheel; the base's refresh should take this over.
RUN \
    set -euo pipefail && \
    w=/venv/main/lib/python3.12/site-packages/nodejs_wheel/bin/node && \
    if [[ -x "$w" && ! -L "$w" ]]; then \
        major=$("$w" --version | cut -d. -f1) && \
        own=$(ls -d /opt/nvm/versions/node/${major}.*/bin/node 2>/dev/null | sort -V | tail -1 || true) && \
        [[ -n "$own" ]] || { echo "FATAL: no image Node on the ${major} line to replace the bundled one"; exit 1; } && \
        ln -sf "$own" "$w"; \
    fi

# CA data that does not follow the OS store on its own, after every install above (ADR 0058).
RUN /opt/instance-tools/lib/refresh-ca-data.sh

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
