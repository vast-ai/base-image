ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py310-2026-06-10

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Stable Diffusion A1111 image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Pin setuptools<78 — legacy packages (e.g. CLIP) use pkg_resources which was
# removed in setuptools 78+. Persists into runtime for the A1111 extension manager.
RUN echo 'setuptools<78' > /etc/pip-constraint.txt
ENV PIP_CONSTRAINT=/etc/pip-constraint.txt

# Required or we will not build
ARG A1111_REF=dev

RUN \
    set -euo pipefail && \
    [[ -n "${A1111_REF}" ]] || { echo "Must specify A1111_REF" && exit 1; } && \
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
    uv pip install --no-cache-dir ${EXTRA_ACCEL_PKGS} torch=="${PYTORCH_VERSION}" --torch-backend "${PYTORCH_BACKEND}" && \
    # onnxruntime-gpu has no aarch64 wheels at any version (manylinux x86_64
    # + win_amd64 only). A1111's ROOP / face-restoration extensions use it
    # when installed; on arm64 we skip the install — the rest of the image
    # still works, those extensions just won't be GPU-accelerated.
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        uv pip install --no-cache-dir onnxruntime-gpu; \
    fi && \
    # Get A1111 and install dependencies (torch should not be pinned to a specific version in the requirements.txt - If it is then our build will probably fail)
    cd /opt/workspace-internal/ && \
    git clone --recursive https://github.com/AUTOMATIC1111/stable-diffusion-webui && \
    cd /opt/workspace-internal/stable-diffusion-webui && \
    git checkout "${A1111_REF}" && \
    # Strip base-provided torch ecosystem pins from upstream requirements so
    # the pytorch base remains the single source of truth. The diff-based
    # assertion at the end of this RUN catches any drift.
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' requirements_versions.txt && \
    uv pip install --no-cache-dir \
        -r requirements_versions.txt && \
    # Download custom models into the build if you want them - None included in default build
    # wget -O /opt/workspace-internal/stable-diffusion-webui/models/Stable-diffusion/model1.safetensors https://url.to.files/model1.safetensors && \
    # wget -O /opt/workspace-internal/stable-diffusion-webui/models/Stable-diffusion/model2.safetensors https://url.to.files/model2.safetensors && \
    # Quick startup test in CPU mode to ensure requirements ready and startup succeeds
    cd /opt/workspace-internal/stable-diffusion-webui && \
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
    # Verify the torch ecosystem (torch + torchvision + torchaudio + torchcodec)
    # versions are unchanged. Catches silent ABI clobbers that the
    # torch-only check used to miss.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
