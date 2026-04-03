ARG AIO_BASE=robatvastai/aio-studio:base-2026-04-01

FROM ${AIO_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="AIO Studio image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# ---------- ComfyUI ----------
ARG COMFYUI_REPO=https://github.com/Comfy-Org/ComfyUI
ARG COMFYUI_REF=v0.18.2
RUN \
    set -euo pipefail && \
    create_app_venv comfyui main && \
    . /venv/comfyui/bin/activate && \
    uv pip install --no-cache-dir xformers sageattention==1.0.6 --torch-backend "${PYTORCH_BACKEND}" && \
    uv pip install --no-cache-dir onnxruntime-gpu insightface && \
    cd /opt/workspace-internal/ && \
    git clone "${COMFYUI_REPO}" ComfyUI && \
    cd ComfyUI && \
    git checkout "${COMFYUI_REF}" && \
    uv pip install --no-cache-dir -r requirements.txt && \
    # Model symlink
    cd models && ln -s checkpoints ckpt && \
    # Install critical custom nodes
    cd /opt/workspace-internal/ComfyUI/custom_nodes && \
    git clone https://github.com/Comfy-Org/ComfyUI-Manager.git && \
    uv pip install --no-cache-dir -r ComfyUI-Manager/requirements.txt && \
    git clone https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint && \
    # Quick startup test in CPU mode
    cd /opt/workspace-internal/ComfyUI && \
    LD_PRELOAD=libtcmalloc_minimal.so.4 \
        python main.py \
            --cpu \
            --listen 127.0.0.1 \
            --port 11404 \
            --disable-auto-launch \
            --quick-test-for-ci && \
    mkdir -p user/default/ComfyUI-Manager/components

ENV COMFYUI_ARGS="--disable-auto-launch --enable-cors-header --port 18188"

# ---------- SD Forge (classic) ----------
ARG FORGE_REPO=https://github.com/Haoming02/sd-webui-forge-classic
ARG FORGE_REF=f357202
RUN \
    set -euo pipefail && \
    create_app_venv forge main && \
    . /venv/forge/bin/activate && \
    uv pip install --no-cache-dir xformers sageattention==1.0.6 --torch-backend "${PYTORCH_BACKEND}" && \
    cd /opt/workspace-internal/ && \
    git clone "${FORGE_REPO}" stable-diffusion-webui-forge && \
    cd stable-diffusion-webui-forge && \
    git checkout "${FORGE_REF}" && \
    # Patch neo's interactive version check
    sed -i 's/    launch_utils.verify_version()/#    launch_utils.verify_version()  # bypassed for Docker/' launch.py && \
    # Pin setuptools<78 for legacy CLIP compatibility
    echo 'setuptools<78' > /tmp/pip-constraint.txt && \
    export PIP_CONSTRAINT=/tmp/pip-constraint.txt && \
    uv pip install --no-cache-dir \
        -r $([[ -f requirements_versions.txt ]] && echo "requirements_versions.txt" || echo "requirements.txt") && \
    # Quick startup test in CPU mode
    LD_PRELOAD=libtcmalloc_minimal.so.4 \
        python launch.py \
            --use-cpu all \
            --skip-torch-cuda-test \
            --skip-python-version-check \
            --no-download-sd-model \
            --do-not-download-clip \
            --no-half \
            --port 11404 \
            --exit

# ---------- Shared models: Forge -> ComfyUI (relative symlinks survive workspace sync) ----------
RUN \
    set -euo pipefail && \
    cd /opt/workspace-internal/stable-diffusion-webui-forge && \
    rm -rf models/Stable-diffusion && \
    ln -s ../../ComfyUI/models/checkpoints models/Stable-diffusion && \
    rm -rf models/Lora && \
    ln -s ../../ComfyUI/models/loras models/Lora && \
    rm -rf models/VAE && \
    ln -s ../../ComfyUI/models/vae models/VAE && \
    rm -rf models/ControlNet && \
    ln -s ../../ComfyUI/models/controlnet models/ControlNet && \
    rm -rf embeddings && \
    ln -s ../ComfyUI/models/embeddings embeddings && \
    rm -rf models/ESRGAN && \
    ln -s ../../ComfyUI/models/upscale_models models/ESRGAN

# ---------- Voicebox (base: torch-2.9.1 — flash-attn wheel requires 2.9) ----------
ARG VOICEBOX_REPO=https://github.com/jamiepine/voicebox
ARG VOICEBOX_REF=v0.3.0
RUN \
    set -euo pipefail && \
    create_app_venv voicebox torch-2.9.1 && \
    . /venv/voicebox/bin/activate && \
    cd /opt/ && \
    git clone "${VOICEBOX_REPO}" voicebox && \
    cd voicebox && \
    git checkout "${VOICEBOX_REF}" && \
    cd backend && \
    sed -i '/^torch>=/d' requirements.txt && \
    uv pip install --no-cache-dir -r requirements.txt && \
    uv pip install --no-cache-dir --no-deps chatterbox-tts && \
    uv pip install --no-cache-dir --no-deps hume-tada && \
    # Build web frontend
    cd /opt/voicebox && \
    . /opt/nvm/nvm.sh && \
    npm install -g bun && \
    sed -i "s#'http://localhost:17493'#window.location.origin#g" web/src/platform/lifecycle.ts && \
    sed -i "s#'http://127.0.0.1:17493'#window.location.origin#g" app/src/stores/serverStore.ts && \
    sed -i '/"tauri"/d; /"landing"/d' package.json && \
    sed -i -z 's/,\n  ]/\n  ]/' package.json && \
    bun install --no-save && \
    cd web && bunx --bun vite build && \
    cp -r dist ../frontend

ENV VOICEBOX_ARGS="--host 127.0.0.1 --port 17493"

# ---------- Ostris AI Toolkit (base: torch-2.7.1) ----------
ARG AI_TOOLKIT_REPO=https://github.com/ostris/ai-toolkit
ARG AI_TOOLKIT_REF=dcb7f46
RUN \
    set -euo pipefail && \
    create_app_venv ostris torch-2.7.1 && \
    . /venv/ostris/bin/activate && \
    . /opt/nvm/nvm.sh && \
    cd /opt/workspace-internal && \
    git clone "${AI_TOOLKIT_REPO}" && \
    cd $(basename ${AI_TOOLKIT_REPO}) && \
    git checkout "${AI_TOOLKIT_REF}" && \
    uv pip install --no-cache-dir timm==1.0.22 && \
    uv pip install --no-cache-dir -r requirements.txt && \
    cd ui && \
    npm install && \
    npm run update_db && \
    npm run build

# ---------- Wan2GP (base: torch-2.7.1) ----------
ARG WAN2GP_REPO=https://github.com/deepbeepmeep/Wan2GP
ARG WAN2GP_REF=8675eab
RUN \
    set -euo pipefail && \
    create_app_venv wan2gp torch-2.7.1 && \
    . /venv/wan2gp/bin/activate && \
    cd /opt/workspace-internal/ && \
    git clone "${WAN2GP_REPO}" Wan2GP && \
    cd Wan2GP && \
    git checkout "${WAN2GP_REF}" && \
    uv pip install --no-cache-dir -r requirements.txt

# ---------- Unsloth Studio (base: main/torch2.10) ----------
ARG UNSLOTH_VERSION=0.1.35-Beta
RUN \
    set -euo pipefail && \
    . /opt/nvm/nvm.sh && \
    create_app_venv unsloth main && \
    . /venv/unsloth/bin/activate && \
    uv pip install --no-cache-dir unsloth && \
    # Store unsloth data under /opt so it survives volume mounts over /root
    mkdir -p /opt/workspace-internal/unsloth/studio && \
    ln -sf /opt/workspace-internal/unsloth /root/.unsloth && \
    # Symlink studio venv -> /venv/unsloth so setup.sh finds an existing venv
    ln -sf /venv/unsloth /opt/workspace-internal/unsloth/studio/unsloth_studio && \
    # Patch install_python_stack.py to use cu128 torch backend
    find /venv/unsloth/lib -name 'install_python_stack.py' -path '*/studio/*' \
        -exec sed -i 's|--torch-backend=auto|--torch-backend=cu128|' {} + && \
    # Build llama.cpp from source (Unsloth pins its own tested version)
    export UNSLOTH_LLAMA_FORCE_COMPILE=1 && \
    export SKIP_STUDIO_BASE=1 && \
    unsloth studio setup && \
    # Copy llama.cpp binaries to a fixed path outside workspace sync
    cp -a /opt/workspace-internal/unsloth/llama.cpp/build/bin /opt/llama-cpp-gpu && \
    echo "/opt/llama-cpp-gpu" > /etc/ld.so.conf.d/llama-cpp.conf && \
    ldconfig

# ---------- Whisper WebUI (base: torch-2.7.1) ----------
ARG WHISPER_REPO=https://github.com/jhj0517/Whisper-WebUI
ARG WHISPER_REF=v1.0.8
RUN \
    set -euo pipefail && \
    create_app_venv whisper torch-2.7.1 && \
    . /venv/whisper/bin/activate && \
    cd /opt/workspace-internal/ && \
    git clone "${WHISPER_REPO}" Whisper-WebUI && \
    cd Whisper-WebUI && \
    git checkout "${WHISPER_REF}" && \
    # Strip torch pins — provided by the base venv
    sed -i '/^torch[>=<]/d; /^torchaudio[>=<]/d; /^--extra-index-url.*pytorch/d' requirements.txt && \
    # Ensure setuptools<78 for legacy pkg_resources in build deps
    uv pip install --no-cache-dir 'setuptools<78' && \
    uv pip install --no-cache-dir --no-build-isolation -r requirements.txt -r backend/requirements-backend.txt

# ---------- ACE Step 1.5 (base: main/torch2.10) ----------
ARG ACE_STEP_REPO=https://github.com/ace-step/ACE-Step-1.5
ARG ACE_STEP_REF=v0.1.5
ARG ACE_STEP_UI_REPO=https://github.com/fspecii/ace-step-ui
ARG ACE_STEP_UI_REF=8f67d6a
RUN \
    set -euo pipefail && \
    create_app_venv ace-step main && \
    . /venv/ace-step/bin/activate && \
    . /opt/nvm/nvm.sh && \
    cd /opt/workspace-internal/ && \
    git clone "${ACE_STEP_REPO}" ACE-Step-1.5 && \
    cd ACE-Step-1.5 && \
    git checkout "${ACE_STEP_REF}" && \
    # Blackwell (compute >= 12) fix: CUDA graph capture fails in nanovllm
    sed -i 's/enforce_eager_for_vllm = bool(is_rocm or is_jetson)/is_blackwell = False\n            try:\n                cc = torch.cuda.get_device_capability()\n                is_blackwell = cc[0] >= 12\n            except Exception:\n                pass\n            enforce_eager_for_vllm = bool(is_rocm or is_jetson or is_blackwell)/' \
        acestep/llm_inference.py && \
    UV_PROJECT_ENVIRONMENT=/venv/ace-step uv sync && \
    uv pip install --no-cache-dir bitsandbytes && \
    # UI looks for .venv in the project root
    ln -s /venv/ace-step .venv && \
    # Clone and install ace-step-ui
    cd /opt/workspace-internal/ && \
    git clone "${ACE_STEP_UI_REPO}" ace-step-ui && \
    cd ace-step-ui && \
    git checkout "${ACE_STEP_UI_REF}" && \
    npm install && \
    cd server && \
    npm install && \
    cp .env.example .env

# ---------- Google Chrome ----------
RUN \
    cd /tmp && \
    curl -o google-chrome-stable.deb -fsSL "https://dl.google.com/linux/direct/google-chrome-stable_current_$(dpkg --print-architecture).deb" && \
    apt-get update && apt-get install -y ./google-chrome-stable.deb && \
    rm -f google-chrome-stable.deb && apt-get clean && rm -rf /var/lib/apt/lists/* && \
    # Point google-chrome-stable at our wrapper which adds --no-sandbox (required for root)
    ln -sf /usr/local/bin/google-chrome /usr/bin/google-chrome-stable

# ---------- Blender ----------
ARG BLENDER_VERSION=""
RUN \
    set -euo pipefail && \
    if [ -z "$BLENDER_VERSION" ]; then \
        MAJOR_MINOR=$(curl -fsSL "https://download.blender.org/release/" | \
            grep -oP 'Blender\K\d+\.\d+' | sort -V | tail -1) && \
        BLENDER_VERSION=$(curl -fsSL "https://download.blender.org/release/Blender${MAJOR_MINOR}/" | \
            grep -oP "blender-\K${MAJOR_MINOR}\.\d+" | sort -V | tail -1); \
    fi && \
    cd /opt && \
    wget "https://download.blender.org/release/Blender${BLENDER_VERSION%.*}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
    tar xf "blender-${BLENDER_VERSION}-linux-x64.tar.xz" && rm "blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
    ln -s "/opt/blender-${BLENDER_VERSION}-linux-x64/blender" /usr/bin/blender && \
    cp "/opt/blender-${BLENDER_VERSION}-linux-x64/blender.desktop" /usr/share/applications && \
    cp "/opt/blender-${BLENDER_VERSION}-linux-x64/blender.svg" /usr/share/icons/hicolor/scalable/apps/

# ==========================================================================
# Final assembly
# ==========================================================================

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# ComfyUI provisioner extension (from comfyui build context)
# Build with: --build-context comfyui=../comfyui
COPY --from=comfyui ROOT/opt/instance-tools/lib/provisioner_comfyui /opt/instance-tools/lib/provisioner_comfyui
COPY --from=comfyui ROOT/opt/instance-tools/bin/ /opt/instance-tools/bin/

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
