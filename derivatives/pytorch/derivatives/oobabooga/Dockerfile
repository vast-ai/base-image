ARG PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312-2026-06-15

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Oobabooga Text Generation WebUI image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG OOBABOOGA_REF

RUN \
    set -euo pipefail && \
    [[ -n "${OOBABOOGA_REF}" ]] || { echo "Must specify OOBABOOGA_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # Snapshot the base's full torch ecosystem; the drift-guard at the end asserts
    # it is byte-identical post-install (the base stays the single source of truth).
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    # Declarative "the torch ecosystem must not move" floor, applied to the upstream
    # requirements install below — the base owns torch. xformers is NOT pinned here:
    # it arrives via the ref's own accel URL (installed --no-deps), internally
    # consistent with the exllamav3 wheel from the same ref.
    printf 'torch==%s\ntorchvision\ntorchaudio\ntorchcodec\n' "${PYTORCH_VERSION}" > /tmp/torch-constraints.txt && \
    ARCH="$(dpkg --print-architecture)" && \
    # Get Oobabooga at the CI-resolved ref (latest upstream HEAD, or a manual ref).
    cd /opt/workspace-internal && \
    git clone https://github.com/oobabooga/text-generation-webui && \
    cd /opt/workspace-internal/text-generation-webui && \
    git checkout "${OOBABOOGA_REF}" && \
    REQ=requirements/full/requirements.txt && \
    # The accel handling is derived from THIS ref's requirements at build time
    # (REF-agnostic) so CI always tracks the latest upstream. There is a real risk
    # a future ref breaks here (a missing cp312 wheel, a new torch minor); that is
    # accepted — it fails the build LOUDLY, and the live-GPU QA gate (ADR 0005) is
    # the runtime net. See ADR 0004.
    # The base is py312; upstream pins its prebuilt CUDA accel wheels to cp313 —
    # rewrite the ABI tag + the python_version marker so they select on py312.
    sed -i 's/cp313-cp313/cp312-cp312/g; s/python_version == "3.13"/python_version == "3.12"/g' "$REQ" && \
    # Base pytorch is the single source of truth for the torch ecosystem.
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' "$REQ" && \
    # Split the externally-hosted accelerator wheels out by STABLE host/org token
    # (REF-agnostic — survives version bumps, unlike a pinned filename). They carry
    # foreign torch/xformers METADATA pins (e.g. exllamav3 -> torch==2.9.0);
    # installing them --no-deps keeps the base torch 2.9.1 (patch-ABI compatible)
    # and avoids a downgrade that would trip the drift-guard. Everything else
    # installs normally under the torch/xformers constraints floor.
    ACCEL_RE='github\.com/(turboderp-org/exllamav3|mjun0812/flash-attention-prebuild-wheels|oobabooga/llama-cpp-binaries)|download\.pytorch\.org/whl/[^ ]*xformers-' && \
    grep -E "$ACCEL_RE" "$REQ" > /tmp/ooba-accel.txt || true && \
    grep -vE "$ACCEL_RE" "$REQ" > /tmp/ooba-rest.txt && \
    if [[ "$ARCH" == "amd64" ]]; then \
        uv pip install --no-cache-dir --no-deps -r /tmp/ooba-accel.txt; \
    fi && \
    uv pip install --no-cache-dir -c /tmp/torch-constraints.txt -r /tmp/ooba-rest.txt && \
    # Download custom models into the build if you want them - None included in default build
    # wget -O ${DATA_DIRECTORY}text-generation-webui/models/model https://url.to.files/model2 && \
    # HARD build-time ABI gate. Import torch FIRST (puts torch/lib on the loader
    # path), then the bare compiled extensions built against torch 2.9.0, resolved
    # against the installed torch 2.9.1. A symbol/ABI mismatch fails the build RED
    # here — no silent degrade. Symbol resolution only (no CUDA device), so it runs
    # on a CPU-only CI runner. Also catches a missed accel extraction (the wheel
    # would be absent and the import would fail). torchao is an additional torch-ABI
    # consumer not import-gated here — a documented QA-gate residual (ADR 0004).
    if [[ "$ARCH" == "amd64" ]]; then \
        python -c "import torch; import exllamav3_ext; import flash_attn.flash_attn_interface; import sys; assert 'exllamav3_ext' in sys.modules and 'flash_attn_2_cuda' in sys.modules, 'accel ABI gate: compiled module did not load'; print('ABI gate OK: exllamav3_ext + flash_attn_2_cuda import against torch', torch.__version__)"; \
    fi && \
    # (4) Verify the torch ecosystem is unchanged.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    { [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }; }

# EXPOSE the Caddy-front (external) ports this image's baked PORTAL_CONFIG
# declares: Instance Portal (1111), Jupyter (8080), Text Generation WebUI (7860),
# Oobabooga API (5000). EXPOSE drives Vast's port auto-map so Caddy proxies them;
# the base overlays via a COPY (not FROM), so its ports are NOT inherited and a
# derivative that bakes a default must declare the full front set itself
# (ADR 0002). Never EXPOSE the loopback internals (11111 / 18080 / 17860 / 15000).
EXPOSE 1111 8080 7860 5000

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
