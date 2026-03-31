ARG PYTORCH_BASE=robatvastai/pytorch:multi-210-291-271-cu128

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="AIO Studio image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Ensure uv pulls CUDA 12.8 wheels for torch ecosystem packages
ENV UV_TORCH_BACKEND=cu128

# ---------- System dependencies (all apps merged) ----------
RUN \
    set -euo pipefail && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        # SD Forge
        libcairo2-dev \
        libxml2-dev \
        libxslt1-dev \
        # Voicebox
        sox \
        # Wan2GP
        libasound2-dev \
        # Unsloth (llama.cpp build)
        libcublas-dev-12-9 \
    && \
    # Make CUDA driver stubs available for linking at build time (no GPU in Docker build)
    echo "/usr/local/cuda/lib64/stubs" > /etc/ld.so.conf.d/cuda-stubs.conf && \
    ldconfig && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ==========================================================================
# Base torch venvs (conda environments from the multi-torch base image)
#   /venv/main        — torch 2.10.0
#   /venv/torch-2.9.1 — torch 2.9.1
#   /venv/torch-2.7.1 — torch 2.7.1
# Shared heavy packages are installed here; app venvs inherit them via
# dist-info copies + .pth. Everything else is per-app.
# ==========================================================================

# flash-attn into torch-2.9.1 (large compiled wheel — shared by all 2.9.1 children)
RUN \
    set -euo pipefail && \
    . /venv/torch-2.9.1/bin/activate && \
    uv pip install --no-cache-dir https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3+cu12torch2.9cxx11abiTRUE-cp312-cp312-linux_x86_64.whl

# ==========================================================================
# Helper: create an isolated app venv sharing only torch from a base venv
# Usage: create_app_venv <app_name> <base_venv>
#
#  - .pth file:       makes base packages importable at runtime (app's own
#                     site-packages takes precedence for any package it installs)
#  - copied dist-info: ONLY for torch ecosystem packages (torch*, nvidia-*,
#                     triton*, cuda-*) so uv sees them as installed and won't
#                     reinstall them. All other packages are installed fresh
#                     per app, maintaining full dependency isolation.
# ==========================================================================
COPY <<'CREATE_APP_VENV' /usr/local/bin/create_app_venv
#!/bin/bash
set -euo pipefail
app=$1; base=$2
uv venv -p /venv/$base/bin/python --relocatable /venv/$app
py_ver=$(ls /venv/$app/lib/)
app_sp="/venv/$app/lib/$py_ver/site-packages"
base_sp="/venv/$base/lib/$py_ver/site-packages"
echo "$base_sp" > "$app_sp/_base.pth"
for di in "$base_sp"/*.dist-info; do
    name=$(basename "$di")
    case "$name" in
        torch-*|torch_*|torchvision-*|torchaudio-*|torchcodec-*|\
        nvidia_*|nvidia-*|triton-*|triton_*|\
        cuda_*|cuda-*|flash_attn-*)
            cp -r "$di" "$app_sp/" ;;
    esac
done
CREATE_APP_VENV
RUN chmod +x /usr/local/bin/create_app_venv

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
    # Skip prebuilt llama.cpp (needs nvidia-smi), go straight to source build
    export UNSLOTH_LLAMA_FORCE_COMPILE=1 && \
    export SKIP_STUDIO_BASE=1 && \
    unsloth studio setup && \
    # Add llama.cpp shared libs to linker search path
    echo "/opt/workspace-internal/unsloth/llama.cpp/build/bin" > /etc/ld.so.conf.d/llama-cpp.conf && \
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

# ==========================================================================
# GPU-accelerated remote desktop (KDE Plasma + Selkies WebRTC + VNC + Guacamole)
# Managed as a single supervisor service: `supervisorctl start/stop desktop`
# ==========================================================================

# --- Display server, VNC, audio, X11 utilities ---
ARG TARGETARCH
RUN \
    set -euo pipefail && \
    add-apt-repository ppa:pipewire-debian/pipewire-upstream -y && \
    add-apt-repository ppa:pipewire-debian/wireplumber-upstream -y && \
    apt-get update && \
    BASE_PACKAGES=( \
        alsa-base alsa-utils coturn dbus \
        fonts-hack fonts-liberation fonts-noto fonts-noto-cjk fonts-noto-color-emoji \
        glib-networking gstreamer1.0-plugins-bad inotify-tools \
        libavcodec-extra libdrm2 libegl1 libgl1 libgles1 libgles2 libglu1 \
        libglvnd0 libglx0 libgudev-1.0-0 mesa-vulkan-drivers libopengl0 \
        libopus0 libpulse0 libsm6 libspa-0.2-bluetooth libspa-0.2-jack \
        libva2 libvdpau-va-gl1 libvdpau1 libwayland-egl1 \
        libx11-6 libx11-xcb1 libxcb-dri3-0 libxcb1 libxdamage1 \
        libxext6 libxfixes3 libxtst6 libxv1 \
        novnc pipewire pipewire-audio-client-libraries pipewire-pulse \
        websockify wireplumber wmctrl \
        x11-apps x11-utils x11-xkb-utils x11-xserver-utils x11vnc \
        xauth xcvt xdg-user-dirs xdg-utils xdotool xfonts-base \
        xinit xkb-data xclip xsel xserver-xorg-core \
        xserver-xorg-input-all xsettingsd xvfb socat \
    ) && \
    I386_PACKAGES=( \
        libc6:i386 libdrm2:i386 libegl1:i386 libgl1:i386 libgles1:i386 \
        libgles2:i386 libglu1:i386 libglvnd0:i386 libglx0:i386 libopengl0:i386 \
        libsm6:i386 libva2:i386 libvulkan-dev:i386 libx11-6:i386 \
        libxcb1:i386 libxext6:i386 libxtst6:i386 libxv1:i386 \
    ) && \
    if [[ $TARGETARCH == "amd64" ]]; then \
        apt-get install --no-install-recommends -y "${BASE_PACKAGES[@]}" "${I386_PACKAGES[@]}"; \
    else \
        apt-get install --no-install-recommends -y "${BASE_PACKAGES[@]}"; \
    fi && \
    ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Vulkan & EGL fallback configs
RUN \
    set -euo pipefail && \
    VULKAN_API_VERSION=$(dpkg -s libvulkan1 | grep -oP 'Version: [0-9|\.]+' | grep -oP '[0-9]+(\.[0-9]+)(\.[0-9]+)') && \
    mkdir -pm755 /etc/vulkan/icd.d/ && \
    printf '{"file_format_version" : "1.0.0","ICD": {"library_path": "libGLX_nvidia.so.0","api_version" : "%s"}}' "$VULKAN_API_VERSION" > /etc/vulkan/icd.d/nvidia_icd.json && \
    mkdir -pm755 /usr/share/glvnd/egl_vendor.d/ && \
    printf '{"file_format_version" : "1.0.0","ICD": {"library_path": "libEGL_nvidia.so.0"}}' > /usr/share/glvnd/egl_vendor.d/10_nvidia.json

# VirtualGL
RUN \
    set -euo pipefail && \
    VIRTUALGL_VERSION="$(curl -fsSL "https://api.github.com/repos/VirtualGL/virtualgl/releases/latest" | jq -r '.tag_name' | sed 's/[^0-9\.\-]*//g')" && \
    cd /tmp && \
    curl -fsSL -O "https://github.com/VirtualGL/virtualgl/releases/download/${VIRTUALGL_VERSION}/virtualgl_${VIRTUALGL_VERSION}_${TARGETARCH}.deb" && \
    apt-get update && apt-get install -y ./virtualgl_${VIRTUALGL_VERSION}_${TARGETARCH}.deb && \
    chmod -f u+s /usr/lib/libvglfaker.so /usr/lib/libvglfaker-nodl.so /usr/lib/libdlfaker.so /usr/lib/libgefaker.so && \
    if [[ $TARGETARCH == "amd64" ]]; then \
        curl -fsSL -O "https://github.com/VirtualGL/virtualgl/releases/download/${VIRTUALGL_VERSION}/virtualgl32_${VIRTUALGL_VERSION}_${TARGETARCH}.deb" && \
        apt-get install -y ./virtualgl32_${VIRTUALGL_VERSION}_${TARGETARCH}.deb; \
    fi && \
    rm -f /tmp/virtualgl* && apt-get clean && rm -rf /var/lib/apt/lists/*

# NVIDIA VAAPI driver (build + clean)
RUN \
    set -euo pipefail && \
    apt-get update && apt-get install --no-install-recommends -y \
        meson pkg-config libc6-dev libegl-dev libglvnd-dev libva-dev \
        libopenh264-dev libvpx-dev libvulkan-dev libwayland-dev \
        libxrandr-dev libgstreamer-plugins-bad1.0-dev libffmpeg-nvenc-dev \
        wayland-protocols libelf-dev && \
    NVIDIA_VAAPI_DRIVER_VERSION="$(curl -fsSL "https://api.github.com/repos/elFarto/nvidia-vaapi-driver/releases/latest" | jq -r '.tag_name' | sed 's/[^0-9\.\-]*//g')" && \
    cd /tmp && \
    curl -fsSL "https://github.com/elFarto/nvidia-vaapi-driver/archive/v${NVIDIA_VAAPI_DRIVER_VERSION}.tar.gz" | tar -xzf - && \
    cd nvidia-vaapi-driver* && \
    meson setup build && meson install -C build && \
    rm -rf /tmp/* && \
    apt-get purge -y --auto-remove \
        meson libegl-dev libglvnd-dev libva-dev libopenh264-dev \
        libvpx-dev libvulkan-dev libwayland-dev libxrandr-dev \
        libgstreamer-plugins-bad1.0-dev libffmpeg-nvenc-dev \
        wayland-protocols libelf-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Selkies GStreamer (low-latency WebRTC streaming)
RUN \
    set -euo pipefail && \
    SELKIES_VERSION="$(curl -fsSL "https://api.github.com/repos/selkies-project/selkies-gstreamer/releases/latest" | jq -r '.tag_name' | sed 's/[^0-9\.\-]*//g')" && \
    cd /opt && curl -fsSL "https://github.com/selkies-project/selkies-gstreamer/releases/download/v${SELKIES_VERSION}/gstreamer-selkies_gpl_v${SELKIES_VERSION}_ubuntu$(grep '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '\"')_$(dpkg --print-architecture).tar.gz" | tar -xzf - && \
    cd /tmp && curl -O -fsSL "https://github.com/selkies-project/selkies-gstreamer/releases/download/v${SELKIES_VERSION}/selkies_gstreamer-${SELKIES_VERSION}-py3-none-any.whl" && \
    pip3 install --no-cache-dir --force-reinstall "selkies_gstreamer-${SELKIES_VERSION}-py3-none-any.whl" "websockets<14.0" && \
    rm -f *.whl && \
    cd /opt && curl -fsSL "https://github.com/selkies-project/selkies-gstreamer/releases/download/v${SELKIES_VERSION}/selkies-gstreamer-web_v${SELKIES_VERSION}.tar.gz" | tar -xzf - && \
    cd /tmp && curl -o selkies-js-interposer.deb -fsSL "https://github.com/selkies-project/selkies-gstreamer/releases/download/v${SELKIES_VERSION}/selkies-js-interposer_v${SELKIES_VERSION}_ubuntu$(grep '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '\"')_$(dpkg --print-architecture).deb" && \
    apt-get update && apt-get install --no-install-recommends -y ./selkies-js-interposer.deb && \
    rm -f *.deb && apt-get clean && rm -rf /var/lib/apt/lists/*


# KDE Plasma desktop environment
RUN \
    set -euo pipefail && \
    apt-get update && apt-get install --install-recommends -y \
        kde-baseapps plasma-desktop plasma-workspace \
        breeze breeze-cursor-theme breeze-gtk-theme breeze-icon-theme \
        dbus-x11 dolphin dolphin-plugins gwenview kwrite kcalc \
        kde-spectacle kfind khotkeys kimageformat-plugins kinfocenter \
        kio kio-extras kmenuedit kmix kwin-addons kwin-x11 \
        fcitx fcitx-frontend-gtk2 fcitx-frontend-gtk3 fcitx-frontend-qt5 \
        fcitx-module-dbus fcitx-module-kimpanel fcitx-module-x11 \
        libreoffice okular vlc systemsettings \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Google Chrome
RUN \
    cd /tmp && \
    curl -o google-chrome-stable.deb -fsSL "https://dl.google.com/linux/direct/google-chrome-stable_current_$(dpkg --print-architecture).deb" && \
    apt-get update && apt-get install -y ./google-chrome-stable.deb && \
    rm -f google-chrome-stable.deb && apt-get clean && rm -rf /var/lib/apt/lists/* && \
    # Point google-chrome-stable at our wrapper which adds --no-sandbox (required for root)
    ln -sf /usr/local/bin/google-chrome /usr/bin/google-chrome-stable

# Blender
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

# Desktop environment variables
ENV XDG_RUNTIME_DIR="/run/user/1001"
ENV DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1001/dbus/session_bus_socket"
ENV DISPLAY=":20"
ENV PIPEWIRE_LATENCY="128/48000"
ENV PIPEWIRE_RUNTIME_DIR="/run/user/1001"
ENV PULSE_SERVER=unix:"/run/user/1001/pulse/native"
ENV PULSE_RUNTIME_PATH="/run/user/1001/pulse"
ENV __GL_SYNC_TO_VBLANK=0
ENV DISPLAY_SIZEW=1920
ENV DISPLAY_SIZEH=1080
ENV DISPLAY_REFRESH=60
ENV DISPLAY_DPI=96
ENV DISPLAY_CDEPTH=24
ENV VGL_DISPLAY=egl
ENV SELKIES_ENABLE_RESIZE=false
ENV SELKIES_ENABLE_BASIC_AUTH=false
ENV GSTREAMER_PATH=/opt/gstreamer
ENV DESKTOP_SESSION=plasma
ENV XDG_SESSION_DESKTOP=KDE
ENV XDG_CURRENT_DESKTOP=KDE
ENV XDG_SESSION_TYPE=x11
ENV KDE_FULL_SESSION=true
ENV KDE_SESSION_VERSION=5
ENV KWIN_COMPOSE=N
ENV KWIN_EFFECTS_FORCE_ANIMATIONS=0
ENV KWIN_EXPLICIT_SYNC=0
ENV KWIN_X11_NO_SYNC_TO_VBLANK=1
ENV GTK_IM_MODULE=fcitx
ENV QT_IM_MODULE=fcitx
ENV XIM=fcitx
ENV XMODIFIERS="@im=fcitx"

RUN mkdir -p "${XDG_RUNTIME_DIR}/dbus"

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
