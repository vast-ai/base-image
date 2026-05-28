ARG AIO_BASE=robatvastai/aio-studio:base-2026-04-01

FROM ${AIO_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="AIO Studio image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Start the instance test runner before provisioning, not after. The current
# AIO_BASE (base-2026-04-01) was built from a pytorch base that still ships
# the runner as 85-instance-test.sh. Rename it to 70- so the test runner's
# HTTP results server is up before provisioning begins. Two-step (mv -n then
# unconditional rm) so a future AIO_BASE rebuild that already ships 70- is
# never clobbered. Becomes a no-op once the AIO_BASE is rebuilt against a
# pytorch base that ships the rename. Mirrors the stopgap in derivatives/
# pytorch/derivatives/comfyui/Dockerfile.
RUN if [ -f /etc/vast_boot.d/85-instance-test.sh ]; then \
        mv -n /etc/vast_boot.d/85-instance-test.sh /etc/vast_boot.d/70-instance-test.sh; \
        rm -f /etc/vast_boot.d/85-instance-test.sh; \
    fi

# xformers, sageattention, and onnxruntime-gpu ship amd64-only artifacts
# (xformers/sageattention CUDA wheels are manylinux_x86_64-only; onnxruntime-gpu
# has no aarch64 wheels at any version). The RUN blocks below use
# `dpkg --print-architecture` rather than ${TARGETARCH:-amd64} so that
# builds without buildx (no auto-injected TARGETARCH) detect the actual
# container arch instead of silently mis-selecting amd64. The Blender
# install handles both arches by selecting the linux-x64 vs linux-arm64
# artifact via the same arch detection. Mirrors the amd64_only pattern
# used by the pytorch base's torch-companions.json.

# ---------- ComfyUI ----------
ARG COMFYUI_REPO=https://github.com/Comfy-Org/ComfyUI
ARG COMFYUI_REF=v0.18.2
RUN \
    set -euo pipefail && \
    create_app_venv comfyui main && \
    . /venv/comfyui/bin/activate && \
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    # Combined with the strips below + the diff-based assertion at the end,
    # this catches silent ABI clobbers — the torch-only check we used to
    # do here let them slip through.
    torch_versions_pre="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        uv pip install --no-cache-dir xformers sageattention==1.0.6 --torch-backend "${PYTORCH_BACKEND}"; \
    fi && \
    # onnxruntime-gpu (and insightface, which uses it for face detection/swap)
    # has no aarch64 wheels at any version. Gate the same way as xformers so
    # arm64 builds skip these — those extensions just won't be available
    # there, the rest of ComfyUI still works.
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        uv pip install --no-cache-dir onnxruntime-gpu insightface; \
    fi && \
    cd /opt/workspace-internal/ && \
    git clone "${COMFYUI_REPO}" ComfyUI && \
    cd ComfyUI && \
    git checkout "${COMFYUI_REF}" && \
    # Strip any base-provided torch ecosystem pins from upstream requirements
    # so the pytorch base remains the single source of truth. ComfyUI does
    # not currently pin these, but the strip is a defensive guard — upstream
    # changes have introduced such pins in the past (see wan2gp /
    # ostris-ai-toolkit cases) and the diff-based assertion at the end of
    # this RUN catches any drift.
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' requirements.txt && \
    # ComfyUI now ships two managers — the legacy ComfyUI-Manager custom
    # node (cloned below) and a built-in manager activated by passing
    # --enable-manager (optionally with --enable-manager-legacy-ui) to
    # main.py. Legacy stays the default so existing templates keep
    # working without changing COMFYUI_ARGS; we also pull in the new
    # manager's deps when the COMFYUI_REF supports it (manager_requirements
    # is only present on recent refs), so a user who DOES add --enable-manager
    # doesn't need an image rebuild.
    if [ -f manager_requirements ]; then \
        sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' manager_requirements && \
        uv pip install --no-cache-dir -r requirements.txt -r manager_requirements comfy-cli; \
    else \
        uv pip install --no-cache-dir -r requirements.txt; \
    fi && \
    # Model symlink
    cd models && ln -s checkpoints ckpt && \
    # Install critical custom nodes
    cd /opt/workspace-internal/ComfyUI/custom_nodes && \
    git clone https://github.com/Comfy-Org/ComfyUI-Manager.git && \
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' ComfyUI-Manager/requirements.txt && \
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
    mkdir -p user/default/ComfyUI-Manager/components && \
    # Verify the torch ecosystem is unchanged. Catches silent ABI clobbers
    # that the torch-only check used to miss.
    torch_versions_post="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

ENV COMFYUI_ARGS="--disable-auto-launch --enable-cors-header --port 18188"

# ---------- SD Forge (classic) ----------
ARG FORGE_REPO=https://github.com/Haoming02/sd-webui-forge-classic
ARG FORGE_REF=f357202
RUN \
    set -euo pipefail && \
    create_app_venv forge main && \
    . /venv/forge/bin/activate && \
    # Snapshot the base's full torch ecosystem.
    torch_versions_pre="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        uv pip install --no-cache-dir xformers sageattention==1.0.6 --torch-backend "${PYTORCH_BACKEND}"; \
    fi && \
    # onnxruntime-gpu has no aarch64 wheels at any version. insightface
    # uses it for face detection / face-swap extensions; on arm64 we skip
    # both — the rest of the image still works, those extensions just
    # won't be available.
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        uv pip install --no-cache-dir onnxruntime-gpu insightface; \
    fi && \
    cd /opt/workspace-internal/ && \
    git clone "${FORGE_REPO}" stable-diffusion-webui-forge && \
    cd stable-diffusion-webui-forge && \
    git checkout "${FORGE_REF}" && \
    # Patch neo's interactive version check
    sed -i 's/    launch_utils.verify_version()/#    launch_utils.verify_version()  # bypassed for Docker/' launch.py && \
    # Strip base-provided torch ecosystem pins from whichever upstream
    # requirements file we install from. Single source of truth = the
    # pytorch base; the diff-based assertion at the end catches drift.
    forge_req="$([[ -f requirements_versions.txt ]] && echo requirements_versions.txt || echo requirements.txt)" && \
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' "$forge_req" && \
    # Pin setuptools<78 for legacy CLIP compatibility
    echo 'setuptools<78' > /tmp/pip-constraint.txt && \
    export PIP_CONSTRAINT=/tmp/pip-constraint.txt && \
    uv pip install --no-cache-dir -r "$forge_req" && \
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
            --exit && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

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
    # Snapshot the base's full torch ecosystem.
    torch_versions_pre="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    cd /opt/ && \
    git clone "${VOICEBOX_REPO}" voicebox && \
    cd voicebox && \
    git checkout "${VOICEBOX_REF}" && \
    cd backend && \
    # Strip base-provided torch ecosystem pins from upstream requirements;
    # the pytorch base is the single source of truth. The diff-based
    # assertion at the end catches drift.
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' requirements.txt && \
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
    cp -r dist ../frontend && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

ENV VOICEBOX_ARGS="--host 127.0.0.1 --port 17493"

# ---------- Ostris AI Toolkit (base: torch-2.9.1 — matches upstream DGX path) ----------
# Invariant: the torch-2.9.1 base ships torchcodec 0.9.1 (per
# torch-companions.json), an exact match for ai-toolkit's pinned
# torchcodec==0.9.1 in requirements_base.txt. Do not move this off
# torch-2.9.1 without simultaneously checking that the new base's
# torchcodec matches the upstream pin — torchcodec's PyPI metadata
# declares no torch requirement, so a version skew won't be caught
# by dependency resolution.
ARG AI_TOOLKIT_REPO=https://github.com/ostris/ai-toolkit
ARG AI_TOOLKIT_REF=dcb7f46
RUN \
    set -euo pipefail && \
    create_app_venv ostris torch-2.9.1 && \
    . /venv/ostris/bin/activate && \
    . /opt/nvm/nvm.sh && \
    # Snapshot the base's full torch ecosystem.
    torch_versions_pre="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    cd /opt/workspace-internal && \
    git clone "${AI_TOOLKIT_REPO}" && \
    cd $(basename ${AI_TOOLKIT_REPO}) && \
    git checkout "${AI_TOOLKIT_REF}" && \
    # Fixes known issue with toolkit dcb7f46
    uv pip install --no-cache-dir timm==1.0.22 && \
    # Defensive strip: with torch-2.9.1 base the torchcodec versions
    # already match (0.9.1 == 0.9.1), so this is belt-and-suspenders
    # against a future upstream pin change. torchao is NOT stripped
    # here: it isn't in the pytorch base, so ai-toolkit's explicit pin
    # is what installs it.
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' requirements_base.txt && \
    uv pip install --no-cache-dir -r requirements.txt && \
    cd ui && \
    npm install && \
    npm run update_db && \
    npm run build && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# ---------- Wan2GP (base: torch-2.7.1) ----------
ARG WAN2GP_REPO=https://github.com/deepbeepmeep/Wan2GP
ARG WAN2GP_REF=8675eab
RUN \
    set -euo pipefail && \
    create_app_venv wan2gp torch-2.7.1 && \
    . /venv/wan2gp/bin/activate && \
    # Snapshot the base's full torch ecosystem.
    torch_versions_pre="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    cd /opt/workspace-internal/ && \
    git clone "${WAN2GP_REPO}" Wan2GP && \
    cd Wan2GP && \
    git checkout "${WAN2GP_REF}" && \
    # Strip all base-provided torch ecosystem pins from upstream
    # requirements. Wan2GP pins torchcodec==0.10.0 but never imports it
    # (dead dep) — without this strip that pin silently clobbers the
    # base. torchcodec's PyPI metadata declares no torch requirement so
    # uv won't catch the skew via dependency resolution; the diff-based
    # assertion at the end is what catches it now.
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' requirements.txt && \
    uv pip install --no-cache-dir -r requirements.txt && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# ---------- Unsloth Studio (base: main/torch2.10) ----------
# Default empty: install whatever PyPI's current `unsloth` resolves to.
# CI passes UNSLOTH_VERSION as a PyPI release version when pinning is
# needed. Don't put a tag-style default here ("0.1.35-Beta") — PyPI
# normalizes that to 0.1.35b0 which doesn't exist as a release.
ARG UNSLOTH_VERSION=
RUN \
    set -euo pipefail && \
    . /opt/nvm/nvm.sh && \
    create_app_venv unsloth main && \
    . /venv/unsloth/bin/activate && \
    # Snapshot the base's full torch ecosystem.
    torch_versions_pre="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    unsloth_spec="unsloth${UNSLOTH_VERSION:+==${UNSLOTH_VERSION}}" && \
    uv pip install --no-cache-dir --torch-backend=cu128 "$unsloth_spec" && \
    # Store unsloth data under /opt so it survives volume mounts over /root
    mkdir -p /opt/workspace-internal/unsloth/studio && \
    ln -sf /opt/workspace-internal/unsloth /root/.unsloth && \
    # Symlink studio venv -> /venv/unsloth so setup.sh finds an existing venv
    ln -sf /venv/unsloth /opt/workspace-internal/unsloth/studio/unsloth_studio && \
    # Patch install_python_stack.py to use cu128 torch backend
    find /venv/unsloth/lib -name 'install_python_stack.py' -path '*/studio/*' \
        -exec sed -i 's|--torch-backend=auto|--torch-backend=cu128|' {} + && \
    # Patch setup.sh to build llama.cpp with portable CPU dispatch instead of
    # -march=native. Without this, the build host's AVX-512 leaks into
    # libggml-cpu.so and SIGILLs on any runtime CPU lacking AVX-512 (e.g.
    # Zen 2 EPYC). GGML_CPU_ALL_VARIANTS produces per-microarch backends
    # selected at runtime via feature detection.
    find /venv/unsloth/lib -name 'setup.sh' -path '*/studio/*' \
        -exec sed -i 's|-DGGML_NATIVE=ON|-DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=ON -DGGML_BACKEND_DL=ON -DBUILD_SHARED_LIBS=ON|' {} + && \
    # Build llama.cpp from source (Unsloth pins its own tested version)
    export UNSLOTH_LLAMA_FORCE_COMPILE=1 && \
    export SKIP_STUDIO_BASE=1 && \
    unsloth studio setup && \
    # Copy llama.cpp binaries to a fixed path outside workspace sync
    cp -a /opt/workspace-internal/unsloth/llama.cpp/build/bin /opt/llama-cpp-gpu && \
    echo "/opt/llama-cpp-gpu" > /etc/ld.so.conf.d/llama-cpp.conf && \
    ldconfig && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# ---------- Whisper WebUI (base: torch-2.7.1) ----------
ARG WHISPER_REPO=https://github.com/jhj0517/Whisper-WebUI
ARG WHISPER_REF=v1.0.8
RUN \
    set -euo pipefail && \
    create_app_venv whisper torch-2.7.1 && \
    . /venv/whisper/bin/activate && \
    # Snapshot the base's full torch ecosystem.
    torch_versions_pre="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    cd /opt/workspace-internal/ && \
    git clone "${WHISPER_REPO}" Whisper-WebUI && \
    cd Whisper-WebUI && \
    git checkout "${WHISPER_REF}" && \
    # Strip base-provided torch ecosystem pins from upstream requirements and
    # drop the --extra-index-url that points pip at the wrong torch wheel
    # index. The pytorch base is the single source of truth; the diff-based
    # assertion at the end catches drift.
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])|^--extra-index-url.*pytorch/d' requirements.txt backend/requirements-backend.txt && \
    # Ensure setuptools<78 for legacy pkg_resources in build deps
    uv pip install --no-cache-dir 'setuptools<78' && \
    uv pip install --no-cache-dir --no-build-isolation -r requirements.txt -r backend/requirements-backend.txt && \
    # Verify torch ecosystem unchanged.
    torch_versions_post="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

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
    # Snapshot the base's full torch ecosystem.
    torch_versions_pre="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    cd /opt/workspace-internal/ && \
    git clone "${ACE_STEP_REPO}" ACE-Step-1.5 && \
    cd ACE-Step-1.5 && \
    git checkout "${ACE_STEP_REF}" && \
    # Blackwell (compute >= 12) fix: CUDA graph capture fails in nanovllm
    sed -i 's/enforce_eager_for_vllm = bool(is_rocm or is_jetson)/is_blackwell = False\n            try:\n                cc = torch.cuda.get_device_capability()\n                is_blackwell = cc[0] >= 12\n            except Exception:\n                pass\n            enforce_eager_for_vllm = bool(is_rocm or is_jetson or is_blackwell)/' \
        acestep/llm_inference.py && \
    # Strip torch ecosystem deps — provided by the base venv
    sed -i '/^[[:space:]]*"torch[>=<;",]/d; /^[[:space:]]*"torchvision[>=<;",]/d; /^[[:space:]]*"torchaudio[>=<;",]/d; /^[[:space:]]*"torchcodec[>=<;",]/d' pyproject.toml && \
    # Fail loudly if upstream pyproject formatting changes and the strip misses a dep —
    # silent passthrough would let uv pip install clobber the base venv's torch stack
    if grep -qE '^[[:space:]]*"(torch|torchvision|torchaudio|torchcodec)([^A-Za-z0-9_-]|$)' pyproject.toml; then \
        echo "ERROR: torch ecosystem deps still present in pyproject.toml after strip:" >&2; \
        grep -nE '^[[:space:]]*"(torch|torchvision|torchaudio|torchcodec)' pyproject.toml >&2; \
        exit 1; \
    fi && \
    uv pip install --no-cache-dir hatchling && \
    uv pip install --no-cache-dir --no-build-isolation ./acestep/third_parts/nano-vllm . && \
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
    cp .env.example .env && \
    # Verify torch ecosystem unchanged. ace-step's pyproject.toml strip
    # above prevents upstream pins from leaking through; this is the
    # downstream sanity check that catches any other clobber path.
    torch_versions_post="$({ uv pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# ---------- Google Chrome (amd64 only — no aarch64 .deb from Google) ----------
RUN \
    set -euo pipefail && \
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        cd /tmp && \
        curl -o google-chrome-stable.deb -fsSL "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb" && \
        apt-get update && apt-get install -y ./google-chrome-stable.deb && \
        rm -f google-chrome-stable.deb && apt-get clean && rm -rf /var/lib/apt/lists/* && \
        # Point google-chrome-stable at our wrapper which adds --no-sandbox (required for root)
        ln -sf /usr/local/bin/google-chrome /usr/bin/google-chrome-stable; \
    else \
        echo "Skipping Google Chrome on $(dpkg --print-architecture); Google does not publish an aarch64 .deb."; \
    fi

# ---------- Blender ----------
# Blender ships ONLY linux-x64 tarballs on the official archive — there is no
# Linux arm64 tarball at any version (verified across 4.x..5.x). So on amd64
# we install the latest official tarball; on arm64 we install Ubuntu's
# distro-packaged blender (older, but a real working .deb). Same pattern as
# derivatives/linux-desktop/Dockerfile.
ARG BLENDER_VERSION=""
RUN \
    set -euo pipefail && \
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        if [ -z "$BLENDER_VERSION" ]; then \
            MAJOR_MINOR=$(curl -fsSL "https://download.blender.org/release/" | \
                grep -oP 'Blender\K\d+\.\d+' | sort -V | tail -1) && \
            BLENDER_VERSION=$(curl -fsSL "https://download.blender.org/release/Blender${MAJOR_MINOR}/" | \
                grep -oP "blender-\K${MAJOR_MINOR}\.\d+" | sort -V | tail -1); \
        fi && \
        echo "Installing Blender ${BLENDER_VERSION} (amd64 tarball)" && \
        cd /opt && \
        wget "https://download.blender.org/release/Blender${BLENDER_VERSION%.*}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
        tar xf "blender-${BLENDER_VERSION}-linux-x64.tar.xz" && rm "blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
        ln -s "/opt/blender-${BLENDER_VERSION}-linux-x64/blender" /usr/bin/blender && \
        cp "/opt/blender-${BLENDER_VERSION}-linux-x64/blender.desktop" /usr/share/applications && \
        cp "/opt/blender-${BLENDER_VERSION}-linux-x64/blender.svg" /usr/share/icons/hicolor/scalable/apps/; \
    else \
        echo "Installing Blender from apt (arm64; no official Linux arm64 tarball exists)" && \
        apt-get update && \
        apt-get install --no-install-recommends -y blender && \
        apt-get clean && rm -rf /var/lib/apt/lists/*; \
    fi

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
