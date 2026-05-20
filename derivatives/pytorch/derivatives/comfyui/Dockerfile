ARG PYTORCH_BASE=vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-04-15

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="ComfyUI image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Start the instance test runner before provisioning, not after.
# The base image still ships the boot script as 85-instance-test.sh (it runs
# after 75-provisioning-manifest.sh); rename it to 70- so the test runner's
# HTTP results server is up before provisioning begins and a client can watch
# it live. This is a stopgap until the base image is rebuilt with the rename
# already in place — at which point 85-instance-test.sh is absent and this
# is a no-op.
#
# Two-step (mv -n then unconditional rm) so a future base image that already
# ships 70- (e.g. partially-applied rename, or a derivative layer added one)
# is never clobbered: mv -n preserves any existing 70-, and the rm cleans up
# any leftover 85- so boot_default.sh does not source two runner launchers.
RUN if [ -f /etc/vast_boot.d/85-instance-test.sh ]; then \
        mv -n /etc/vast_boot.d/85-instance-test.sh /etc/vast_boot.d/70-instance-test.sh; \
        rm -f /etc/vast_boot.d/85-instance-test.sh; \
    fi

# Required or we will not build
ARG COMFYUI_REF
ARG TARGETARCH

RUN \
    set -euo pipefail && \
    [[ -n "${COMFYUI_REF}" ]] || { echo "Must specify COMFYUI_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # xformers and sageattention publish CUDA-only amd64 wheels; skip on other
    # arches (e.g. aarch64/sbsa for Grace Blackwell) so the build does not fail
    # on dependency resolution. Mirrors the amd64_only pattern used by the
    # pytorch base's torch-companions.json.
    EXTRA_ACCEL_PKGS="" && \
    if [[ "${TARGETARCH:-amd64}" == "amd64" ]]; then \
        EXTRA_ACCEL_PKGS="xformers sageattention==1.0.6"; \
    fi && \
    # Fail build on dependency resolution if matching version is unavailable
    uv pip install --no-cache-dir torch=="${PYTORCH_VERSION}" ${EXTRA_ACCEL_PKGS} --torch-backend "${PYTORCH_BACKEND}" && \
    # Get ComfyUI and install dependencies (torch should not be pinned to a specific version in the requirements.txt - If it is then our build will probably fail)
    cd /opt/workspace-internal/ && \
    git clone https://github.com/Comfy-Org/ComfyUI && \
    cd /opt/workspace-internal/ComfyUI && \
    git checkout "${COMFYUI_REF}" && \
    # ComfyUI ships two managers now: the legacy ComfyUI-Manager custom node
    # (cloned below) and a built-in manager activated by passing --enable-manager
    # (optionally with --enable-manager-legacy-ui) to main.py. The legacy
    # manager stays the default so existing templates keep working without
    # changing COMFYUI_ARGS. When the COMFYUI_REF supports it, we also pull
    # in the new manager's deps (manager_requirements) and comfy-cli, so a
    # user who DOES add --enable-manager to COMFYUI_ARGS doesn't need an
    # image rebuild — the new path just works. manager_requirements is only
    # present on recent ComfyUI refs, so guard the install: older tags
    # (which we still want to rebuild with the test runner reorder) keep
    # building cleanly, just without the new-manager opt-in available.
    if [ -f manager_requirements ]; then \
        uv pip install --no-cache-dir -r requirements.txt -r manager_requirements comfy-cli; \
    else \
        uv pip install --no-cache-dir -r requirements.txt; \
    fi && \
    # Avoid Jupyter directory display bug
    cd /opt/workspace-internal/ComfyUI/models/ && \
    ln -s checkpoints ckpt && \
    # Install critical custom nodes
    cd /opt/workspace-internal/ComfyUI/custom_nodes && \
    git clone https://github.com/Comfy-Org/ComfyUI-Manager && \
    uv pip install --no-cache-dir -r ComfyUI-Manager/requirements.txt && \
    git clone https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint && \
    # Quick startup test in CPU mode to ensure requirements ready and startup succeeds
    cd /opt/workspace-internal/ComfyUI && \
    LD_PRELOAD=libtcmalloc_minimal.so.4 \
         python main.py \
            --cpu \
            --listen 127.0.0.1 \
            --port 11404 \
            --disable-auto-launch \
            --quick-test-for-ci && \
    mkdir -p /opt/workspace-internal/ComfyUI/user/default/ComfyUI-Manager/components && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

ENV COMFYUI_ARGS="--disable-auto-launch --enable-cors-header --port 18188"

# Install API wrapper
RUN \
    set -euo pipefail && \
    apt-get update && \
    apt-get install -y --no-install-recommends libmagic1 && \
    cd /opt && \
    git clone https://github.com/ai-dock/comfyui-api-wrapper && \
    cd comfyui-api-wrapper && \
    uv venv -p 3.12 && \
    . .venv/bin/activate && \
    uv pip install -r requirements.txt && \
    deactivate && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Generally we do not ship with models, but this is used for benchmarking the comfyui-json pyworker
# Always use a new RUN or COPY for each model added to keep layers to a reasonable size
RUN \
    set -euo pipefail && \
    mkdir /opt/model_store && \
    . /venv/main/bin/activate && \
    hf download Comfy-Org/stable-diffusion-v1-5-archive v1-5-pruned-emaonly-fp16.safetensors \
        --local-dir "/opt/model_store" && \
    # Symlink stored models to avoid moving large file to overlay/volume 
    ln -s /opt/model_store/v1-5-pruned-emaonly-fp16.safetensors \
        /opt/workspace-internal/ComfyUI/models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors && \
    rm -rf /opt/model_store/.cache && \
    rm -rf /root/.cache/huggingface

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
