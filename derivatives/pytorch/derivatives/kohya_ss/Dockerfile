ARG PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py311-2026-06-15

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Stable Diffusion A1111 image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG KOHYA_REF

# Kohya's GUI provides some great install scripts for local use, but we will manually configure everything we need
RUN \
    [[ -n "${KOHYA_REF}" ]] || { echo "Must specify KOHYA_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    # xformers publishes amd64-only CUDA wheels; skip on other arches
    # (e.g. aarch64/sbsa for Grace Blackwell) so dependency resolution does
    # not fail the build. The strip on requirements_linux.txt below already
    # makes xformers optional at runtime. dpkg --print-architecture reflects
    # the actual container arch — unlike ${TARGETARCH:-amd64}, it doesn't
    # silently mis-select amd64 when buildx hasn't injected TARGETARCH.
    EXTRA_ACCEL_PKGS="" && \
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        EXTRA_ACCEL_PKGS="xformers"; \
    fi && \
    # Pin to the inherited torch version. Fail build on dependency resolution if matching version is unavailable
    uv pip install --no-cache-dir ${EXTRA_ACCEL_PKGS} torch=="${PYTORCH_VERSION}" --torch-backend "${PYTORCH_BACKEND}" && \
    # Get Kohya and install dependencies (We will unpin torch & xformers)
    cd /opt/workspace-internal/ && \
    git clone --recursive https://github.com/bmaltais/kohya_ss.git && \
    cd /opt/workspace-internal/kohya_ss && \
    git checkout "${KOHYA_REF}" && \
    git submodule update --init --recursive && \
    # Strip the four base-provided torch ecosystem packages (uniform pattern
    # across all pytorch-base derivatives) plus xformers/tensorboard/tensorflow
    # and the --extra-index-url line that points pip at the wrong index.
    # The diff-based torch ecosystem assertion at the end of this RUN catches
    # any drift.
    grep -vE '^--extra-index-url|^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])|^xformers==|^tensorboard|^tensorflow' requirements_linux.txt > requirements_linux_modified.txt && \
    uv pip install --no-cache-dir \
        torch=="${PYTORCH_VERSION}" \
        -r requirements_linux_modified.txt && \
    rm -f requirements_linux_modified.txt && \
    # Fix the Tensorboard URL in the GUI to avoid hardcoded localhost links
    sed -i 's|link=f"http://localhost:{self.tensorboard_port}"|link=f"http://{os.environ.get('\''PUBLIC_IPADDR'\'')}:{os.environ.get('\''VAST_TCP_PORT_6006'\'')}"|' \
        /opt/workspace-internal/kohya_ss/kohya_gui/class_tensorboard.py && \
    # Set up a default accelerate profile
    mkdir -p "/root/.cache/huggingface/accelerate" && \
    cp config_files/accelerate/default_config.yaml /root/.cache/huggingface/accelerate/default_config.yaml && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
