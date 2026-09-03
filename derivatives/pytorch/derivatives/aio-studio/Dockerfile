# AIO_BASE carries NO default ON PURPOSE, for the reason ADR 0018 records for
# LLAMA_CPP_VERSION: a default here is a SECOND source of truth for the same fact,
# and it silently loses. The workflow always passes --build-arg AIO_BASE (from its
# `AIO_BASE` input, falling back to `DEFAULT_AIO_BASE`), so the value baked here was
# only ever consulted by a plain `docker build` -- where it pointed at
# <staging-ns>/aio-studio:base-2026-04-01, a tag from a base build that FAILED, in the
# STAGING namespace, four and a half months stale. That is worse than no default: it
# builds, and it builds on the wrong thing. Fail loudly instead.
#
# The base is a SEPARATE image on purpose and stays that way: it is large, Vast hosts
# cache its layers, and an app-layer update is then a small delta rather than a full
# re-pull. Folding it into this Dockerfile would defeat that.
ARG AIO_BASE

FROM ${AIO_BASE}

# Guard the ARG at the top of the build rather than letting a missing value surface as
# a confusing failure deep in an app install. `FROM ${AIO_BASE}` with an empty ARG is
# itself an error, so this mainly catches a value that resolved to something unusable.
#
# THE VENVS ARE THE REAL CONTRACT. `create_app_venv <app> <base>` resolves /venv/$base,
# so a base that does not carry every venv the apps below ask for fails partway through
# a multi-hour build with an error that names uv, not the base. The list is asserted
# here, once, against what this file actually uses:
#
#   main         comfyui, forge, unsloth, ace-step
#   torch-2.7.1  ostris, wan2gp, whisper
#   torch-2.9.1  voicebox
#
# This is also what pins the base to the RIGHT multi-torch family. vastai/pytorch
# publishes several (multi-210-291-271, multi-2130-2121, multi-2130-2121-2110); only
# the first carries 2.9.1 and 2.7.1, so "upgrade to the newest multi base" would remove
# four apps' torch and is caught here rather than in the app layer.
RUN set -euo pipefail && \
    missing=""; \
    for v in main torch-2.7.1 torch-2.9.1; do \
        [ -x "/venv/$v/bin/python" ] || missing="$missing $v"; \
    done; \
    if [ -n "$missing" ]; then \
        echo "FATAL: AIO_BASE lacks the venv(s):$missing"; \
        echo "  present: $(ls -1 /venv 2>/dev/null | tr '\n' ' ')"; \
        echo "  this base is not built on the multi-210-291-271 pytorch family"; \
        exit 1; \
    fi; \
    echo "base venv contract OK: $(ls -1 /venv | tr '\n' ' ')"

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="AIO Studio image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

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
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        uv pip install --no-cache-dir xformers sageattention==1.0.6 --torch-backend "${PYTORCH_BACKEND}"; \
    fi && \
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
# llama.cpp comes from the Unsloth fork's PREBUILT portable bundle, matching the
# standalone unsloth-studio image (ADR 0018). This image bundles the same application,
# so it should install it the same way — and until 2026-09-03 it did not: the standalone
# moved to the prebuilt bundle while this file kept source-compiling, which is exactly
# the drift that makes a bundled copy worse than the thing it copies.
#
# What the source build cost here, identically to the standalone: a 20-40 minute CUDA
# compile on every image build; a FABRICATED nvidia-smi stub, because setup.sh gates
# -DGGML_CUDA=ON on a runtime GPU probe `docker build` cannot satisfy and without it
# silently produced a CPU-only binary (ADR 0016); and a hand-pinned arch list
# (80;86;89;90;100;120) with NO sm_75, so Turing had no kernel image at all.
#
# LLAMA_CPP_VERSION carries no default ON PURPOSE, for the reason ADR 0018 records: it
# is RESOLVED from the publisher in CI (check-github-release against unslothai/llama.cpp)
# and passed in, so a default here would be a second, silently-stale source of truth.
ARG LLAMA_CPP_VERSION
ARG LLAMA_BUNDLE=cuda12-portable
ENV LLAMA_CPP_RELEASE=${LLAMA_CPP_VERSION}
RUN \
    set -euo pipefail && \
    [[ -n "${LLAMA_CPP_VERSION}" ]] || { echo "Must specify LLAMA_CPP_VERSION"; exit 1; } && \
    case "$(dpkg --print-architecture)" in \
        amd64) arch_slug=x64 ;; \
        *) echo "aio-studio is amd64-only; got $(dpkg --print-architecture)"; exit 1 ;; \
    esac && \
    package_name="app-${LLAMA_CPP_VERSION}-linux-${arch_slug}-${LLAMA_BUNDLE}.tar.gz" && \
    base_url="https://github.com/unslothai/llama.cpp/releases/download/${LLAMA_CPP_VERSION}" && \
    mkdir -p /opt/llama-cpp/build/bin /tmp/llama-dl && \
    wget -O "/tmp/llama-dl/${package_name}" "${base_url}/${package_name}" && \
    wget -O /tmp/llama-dl/sha256.json "${base_url}/llama-prebuilt-sha256.json" && \
    python3 -c "\
import json,hashlib,sys;\
name=sys.argv[1];\
m=json.load(open('/tmp/llama-dl/sha256.json')).get('artifacts',{});\
e=m.get(name) or sys.exit('no sha256 entry for '+name+' in llama-prebuilt-sha256.json');\
want=e.get('sha256') or sys.exit('empty sha256 entry for '+name);\
h=hashlib.sha256(open('/tmp/llama-dl/'+name,'rb').read()).hexdigest();\
sys.exit(0) if h==want else sys.exit('sha256 mismatch for '+name+': got '+h+' want '+want)\
" "${package_name}" && \
    python3 -c "\
import sys, tarfile;\
src, dest = sys.argv[1], sys.argv[2];\
tarfile.open(src).extractall(dest, filter='data')" \
        "/tmp/llama-dl/${package_name}" /opt/llama-cpp/build/bin && \
    rm -rf /tmp/llama-dl && \
    for _m in UNSLOTH_PREBUILT_INFO.json THIRD_PARTY_LICENSES.txt; do \
        if [[ -f "/opt/llama-cpp/build/bin/${_m}" ]]; then mv "/opt/llama-cpp/build/bin/${_m}" /opt/llama-cpp/; fi; \
    done && \
    test -x /opt/llama-cpp/build/bin/llama-server || \
        { echo "FATAL: bundle ${package_name} contains no build/bin/llama-server"; exit 1; } && \
    test -f /opt/llama-cpp/build/bin/libggml-cuda.so || \
        { echo "FATAL: bundle ${package_name} ships no CUDA backend"; exit 1; } && \
    test -f /opt/llama-cpp/THIRD_PARTY_LICENSES.txt || \
        { echo "FATAL: bundle ${package_name} ships no third-party licence notice"; exit 1; } && \
    sha256sum /opt/llama-cpp/build/bin/llama-server > /opt/llama-cpp.server.sha256

ARG UNSLOTH_VERSION=0.1.35-Beta
RUN \
    set -euo pipefail && \
    # cuda-cuobjdump is still needed: the SASS arch-coverage assertion below reads the
    # SHIPPED binary. The cublas/cudart DEV packages the source build linked against are
    # no longer required — the bundle is prebuilt — but the runtime libs must still be
    # present for libggml-cuda.so to LOAD, which `ldd -r` below proves.
    apt-get update && \
    apt-get install -y --no-install-recommends cuda-cuobjdump-12-9 && \
    rm -rf /var/lib/apt/lists/* && ldconfig && \
    . /opt/nvm/nvm.sh && \
    create_app_venv unsloth main && \
    . /venv/unsloth/bin/activate && \
    uv pip install --no-cache-dir unsloth && \
    mkdir -p /opt/workspace-internal/unsloth/studio && \
    ln -sf /opt/workspace-internal/unsloth /root/.unsloth && \
    ln -sf /venv/unsloth /opt/workspace-internal/unsloth/studio/unsloth_studio && \
    find /venv/unsloth/lib -name 'install_python_stack.py' -path '*/studio/*' \
        -exec sed -i 's|--torch-backend=auto|--torch-backend=cu128|' {} + && \
    # Point setup.sh at the VERIFIED bundle. Because this path differs from the canonical
    # install dir, setup.sh symlinks the canonical dir at it and sets
    # _NEED_LLAMA_SOURCE_BUILD=false + _SKIP_PREBUILT_INSTALL=true, so it neither
    # downloads (its resolver needs a real driver, absent here) nor compiles.
    #
    # Fail-closed by construction: if a future studio release drops that reuse branch,
    # setup falls through to its own prebuilt path (unavailable without a driver) and
    # then to a source build with no nvcc on PATH — which produces no libggml-cuda.so,
    # so the assertions below fail the build rather than shipping a CPU-only binary.
    export UNSLOTH_LOCAL_LLAMA_CPP_DIR=/opt/llama-cpp && \
    export SKIP_STUDIO_BASE=1 && \
    unsloth studio setup && \
    # PROVE the reuse branch fired — do not infer it. Two independent facts: the
    # canonical install path resolves to our tree, AND the binary is byte-identical to
    # the one whose sha256 was verified before setup ran.
    [[ "$(readlink -f /opt/workspace-internal/unsloth/llama.cpp)" == "/opt/llama-cpp" ]] || \
        { echo "FATAL: studio setup did not reuse /opt/llama-cpp — it built or downloaded its own llama.cpp"; exit 1; } && \
    sha256sum -c /opt/llama-cpp.server.sha256 >/dev/null || \
        { echo "FATAL: llama-server changed during studio setup — the verified bundle was replaced"; exit 1; } && \
    rm -f /opt/llama-cpp.server.sha256 && \
    # The binary must EXECUTE: the fork links llama-server against OpenSSL, so `test -x`
    # can pass on a binary that dies at load time.
    /opt/llama-cpp/build/bin/llama-server --version || \
        { echo "FATAL: llama-server does not execute — its link closure is incomplete"; exit 1; } && \
    # GGML_BACKEND_DL=ON, so a libggml-cuda.so whose dlopen fails is skipped SILENTLY
    # and every inference runs on CPU. `ldd -r` EXITS 0 while printing `undefined
    # symbol`, so the OUTPUT is the check, not the status. Gated by L056.
    export PATH="/usr/local/cuda/bin:${PATH}" && \
    ldd -r /opt/llama-cpp/build/bin/libggml-cuda.so > /tmp/ggml-cuda.ldd 2>&1; \
    if grep -qE "not found|undefined symbol" /tmp/ggml-cuda.ldd; then \
        echo "FATAL: libggml-cuda.so does not resolve:"; cat /tmp/ggml-cuda.ldd; exit 1; \
    fi && \
    rm -f /tmp/ggml-cuda.ldd && \
    # SASS coverage read from the BINARY, not the bundle's manifest. Bracket the QA
    # template's floor: sm_80 (its compute_cap gte 800) and sm_120 (consumer Blackwell).
    # The bundle also carries sm_75, but admitting Turing is a SEPARATE step gated on a
    # live run on Turing hardware (ADR 0018) — image first, floor second. Capture ONCE
    # then match in-shell: piping into `grep -q` SIGPIPEs cuobjdump under pipefail and
    # reports failure on a MATCH. Gated by L081.
    _elf="$(cuobjdump --list-elf /opt/llama-cpp/build/bin/libggml-cuda.so)" && \
    for _a in sm_80 sm_120; do \
        [[ "$_elf" == *"$_a"* ]] || \
            { echo "FATAL: libggml-cuda.so has no $_a SASS — wrong bundle for this image's GPU floor"; exit 1; }; \
    done && \
    # The runtime loader needs the bundle's shared objects on the path. The source build
    # copied them to /opt/llama-cpp-gpu; the bundle already lives outside the workspace
    # sync, so point at it directly rather than duplicating a gigabyte.
    echo "/opt/llama-cpp/build/bin" > /etc/ld.so.conf.d/llama-cpp.conf && \
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
    cp .env.example .env

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
