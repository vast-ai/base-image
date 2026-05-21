ARG PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py311-2026-04-15

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Stable Diffusion Forge image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

RUN apt-get update && apt-get install -y \
    pkg-config \
    libcairo2-dev \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Required or we will not build
ARG FORGE_REPO
ARG FORGE_REF

RUN \
    set -euo pipefail && \
    [[ -n "${FORGE_REPO}" && -n "${FORGE_REF}" ]] || { echo "Must specify FORGE_REPO and FORGE_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    # xformers publishes amd64-only CUDA wheels; skip on other arches
    # (e.g. aarch64/sbsa for Grace Blackwell) so dependency resolution does
    # not fail the build. dpkg --print-architecture reflects the actual
    # container arch — unlike ${TARGETARCH:-amd64}, it doesn't silently
    # mis-select amd64 when buildx hasn't injected TARGETARCH.
    EXTRA_ACCEL_PKGS="" && \
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        EXTRA_ACCEL_PKGS="xformers"; \
    fi && \
    # Pin to the inherited torch version. Fail build on dependency resolution if matching version is unavailable
    uv pip install --no-cache-dir ${EXTRA_ACCEL_PKGS} torch==$PYTORCH_VERSION --torch-backend "${PYTORCH_BACKEND}" && \
    uv pip install --no-cache-dir onnxruntime-gpu insightface && \
    # Get Forge and install dependencies (torch should not be pinned to a specific version in the requirements.txt - If it is then our build will probably fail)
    cd /opt/workspace-internal/ && \
    git clone "${FORGE_REPO}" /opt/workspace-internal/stable-diffusion-webui-forge && \
    cd /opt/workspace-internal/stable-diffusion-webui-forge && \
    git checkout "${FORGE_REF}" && \
    # Patch neo's interactive version check
    sed -i 's/    launch_utils.verify_version()/#    launch_utils.verify_version()  # bypassed for Docker/'  launch.py && \
    # Strip base-provided torch ecosystem pins from whichever upstream
    # requirements file we install from. Single source of truth = the
    # pytorch base; the diff-based assertion at the end catches drift.
    forge_req="$([[ -f requirements_versions.txt ]] && echo requirements_versions.txt || echo requirements.txt)" && \
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' "$forge_req" && \
    uv pip install --no-cache-dir -r "$forge_req" && \
    # Download custom models into the build if you want them - None included in default build
    # wget -O /opt/workspace-internal/stable-diffusion-webui-forge/models/Stable-diffusion/model1.safetensors https://url.to.files/model1.safetensors && \
    # wget -O /opt/workspace-internal/stable-diffusion-webui-forge/models/Stable-diffusion/model2.safetensors https://url.to.files/model2.safetensors && \
    # Pin setuptools<78 for pip build isolation — legacy packages (e.g. CLIP) use
    # pkg_resources which was removed in setuptools 78+. PIP_CONSTRAINT ensures pip's
    # isolated build environments also respect this cap.
    echo 'setuptools<78' > /tmp/pip-constraint.txt && \
    export PIP_CONSTRAINT=/tmp/pip-constraint.txt && \
    # Quick startup test in CPU mode to ensure requirements ready and startup succeeds
    cd /opt/workspace-internal/stable-diffusion-webui-forge && \
    LD_PRELOAD=libtcmalloc_minimal.so.4 \
        python launch.py \
            --use-cpu all \
            --skip-torch-cuda-test \
            --skip-python-version-check \
            --no-download-sd-model \
            --do-not-download-clip \
            --no-half \
            --port 11404 \
            --exit && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
