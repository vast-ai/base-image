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
    # requirements install below. xformers is pinned to 0.0.33 — the version
    # exllamav3 0.0.34 expects; cp39-abi3, so not torch-ABI-coupled.
    printf 'torch==%s\ntorchvision\ntorchaudio\ntorchcodec\nxformers==0.0.33\n' "${PYTORCH_VERSION}" > /tmp/torch-constraints.txt && \
    ARCH="$(dpkg --print-architecture)" && \
    # Get Oobabooga
    cd /opt/workspace-internal && \
    git clone https://github.com/oobabooga/text-generation-webui && \
    cd /opt/workspace-internal/text-generation-webui && \
    git checkout "${OOBABOOGA_REF}" && \
    # (1) Derivative-OWNED accelerator wheels (ADR 0004), installed --no-deps so
    #     their wheel-METADATA torch==2.9.0 / xformers==0.0.33 hard-pins are never
    #     resolved and cannot downgrade the base torch 2.9.1 (patch-ABI compatible).
    #     x86_64-only wheels — skip on other arches rather than mis-resolve.
    if [[ "$ARCH" == "amd64" ]]; then \
        uv pip install --no-cache-dir --no-deps -r /opt/accel-wheels.txt; \
    fi && \
    # (2) The rest of upstream's requirements, MINUS the lines we own: the accel
    #     wheel URLs (removed by STABLE host/package token, never by version-bearing
    #     filename) and the base-provided torch ecosystem. The constraints file
    #     pins the torch ecosystem so nothing in the long tail can move it. We do
    #     NOT sed-rewrite upstream markers: the cp313 accel lines are gated
    #     python_version=="3.13" and simply do not select on this py312 base.
    grep -vE '^[[:space:]]*(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!~;]|$)' requirements/full/requirements.txt \
      | grep -vE 'github\.com/(turboderp-org/exllamav3|mjun0812/flash-attention-prebuild-wheels|oobabooga/llama-cpp-binaries)/' \
      | grep -vE 'download\.pytorch\.org/whl/[^ ]*xformers-' \
      > /tmp/ooba-rest.txt && \
    uv pip install --no-cache-dir -c /tmp/torch-constraints.txt -r /tmp/ooba-rest.txt && \
    # Download custom models into the build if you want them - None included in default build
    # wget -O ${DATA_DIRECTORY}text-generation-webui/models/model https://url.to.files/model2 && \
    # (3) HARD build-time ABI gate (ADR 0004 binding condition 1). Import torch
    #     FIRST (this puts torch/lib on the loader path), then the bare compiled
    #     extensions built against torch 2.9.0, resolved against the installed
    #     torch 2.9.1. A symbol/ABI mismatch fails the build RED here — there is no
    #     silent degrade. This resolves symbols only (no CUDA device needed), so it
    #     runs on a CPU-only CI runner. torchao (installed above under the torch
    #     constraints) is an additional torch-ABI consumer not import-gated here;
    #     it is a documented QA-gate residual (ADR 0004).
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
