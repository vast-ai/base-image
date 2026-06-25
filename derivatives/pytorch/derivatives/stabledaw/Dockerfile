# torch-2.7.1 base: matches theDAW's pinned torch/torchaudio exactly, so the
# strip-pins + drift-guard keeps the base ecosystem (torch 2.7.1, torchaudio
# 2.7.1, torchvision 0.22.1, torchcodec 0.5) authoritative with no version gap.
ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312-2026-04-15
FROM ${PYTORCH_BASE}

LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="StableDAW suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

COPY ./ROOT /

# StableDAW (active fork: gantasmo/theDAW) is commit-pinned — the repo ships no
# releases/tags. The frontend is served same-origin on the backend port; see
# ROOT/opt/stabledaw-serve.py.
ARG STABLEDAW_REPO=https://github.com/gantasmo/theDAW
ARG STABLEDAW_REF
RUN set -euo pipefail; \
    [[ -n "${STABLEDAW_REF}" ]] || { echo "Must specify STABLEDAW_REF"; exit 1; }; \
    . /venv/main/bin/activate; \
    . /opt/nvm/nvm.sh; \
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)"; \
    # ffmpeg is required for every audio path (effects, exports, library ingest,
    # MIDI conversion, URL import) — core to the DAW, not optional.
    apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    cd /opt && \
    git clone "${STABLEDAW_REPO}" stabledaw && \
    cd /opt/stabledaw && \
    git checkout "${STABLEDAW_REF}" && \
    # Strip the base-provided torch ecosystem pins from pyproject.toml so the
    # pytorch base stays the single source of truth. Upstream targets torch
    # 2.7.1 on Linux; the base ships a newer torch. The diff-based drift
    # assertion at the end of this RUN fails the build if anything clobbers it.
    sed -i -E '/^[[:space:]]*"(torch|torchvision|torchaudio|torchcodec)["=<>~!]/d' pyproject.toml && \
    # Drop basic-pitch: it hard-pins tensorflow<2.15.1, which ships no cp312
    # wheels (upstream targets py3.10; this base is py3.12). This disables
    # basic-pitch's MIDI-transcription engine only — the torch-based
    # piano-transcription-inference engine remains, and the midi module guards
    # the import. Revisit if a py3.10 base or a tf-2.16+ basic-pitch lands.
    sed -i -E '/^[[:space:]]*"basic-pitch["=<>~!]/d' pyproject.toml && \
    # hatchling build-backend installs the stable_audio_3 package + all deps;
    # backend/ runs from this source tree (not installed as a package).
    # flash-attn is win32-only here (sys_platform marker), so it is not
    # installed on linux — the Medium model still runs, just without it.
    uv pip install --no-cache-dir . && \
    # Build the React/Vite SPA. The built bundle calls /api/* relative, so
    # serving it same-origin on the backend port needs no API-base patch.
    # Vite 7 requires node >= 20.19, so select an explicit LTS rather than
    # relying on the base nvm default.
    nvm install 22 > /dev/null && \
    nvm use 22 > /dev/null && \
    cd /opt/stabledaw/frontend && \
    npm ci && \
    npm run build && \
    { [[ -f dist/index.html ]] || { echo "frontend build produced no dist/index.html"; exit 1; }; } && \
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    { [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem drift for stabledaw"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }; }

# EXPOSE the Caddy-front (external) ports this image's baked PORTAL_CONFIG
# declares: Instance Portal (1111), Jupyter (8080), StableDAW (8600). EXPOSE
# drives Vast's port auto-map so Caddy proxies them; the base overlays via a
# COPY (not FROM), so its ports are NOT inherited and a derivative that bakes a
# default must declare the full front set itself (ADR 0002). Never EXPOSE the
# internal loopback binds (11111 / 18080 / 18600).
EXPOSE 1111 8080 8600

RUN env-hash > /.env_hash
