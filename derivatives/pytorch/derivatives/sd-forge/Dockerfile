ARG PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py311-2026-09-01

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
    # onnxruntime-gpu has no aarch64 wheels at any version (manylinux x86_64
    # + win_amd64 only). insightface uses it for face detection / face-swap
    # extensions; on arm64 we skip both — the rest of the image still works,
    # those extensions just won't be available. Same shape as the wan2gp
    # onnxruntime block.
    if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then \
        uv pip install --no-cache-dir onnxruntime-gpu insightface; \
    fi && \
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
    # OpenCV must be the HEADLESS build. The GUI wheel bundles Qt — opencv-python
    # 5.0.0.93 ships a `cv2/qt/` plugin tree (29 Qt/xcb entries; the headless wheel of
    # the same version ships none) — and in a container with no X server loading it
    # aborts the process: `Could not load the Qt platform plugin "xcb"`, then `Aborted`.
    # Forge itself survives, so supervisord keeps reporting RUNNING while whichever
    # subprocess touched cv2 is gone.
    #
    # Rewrite the PIN, do not swap the installed package afterwards. `requirements_met()`
    # resolves every pinned name through `importlib.metadata.version()`, so an uninstalled
    # `opencv-python` raises, the check returns False, and Forge reinstalls the WHOLE
    # requirements file — non-headless opencv included — on every container start. Editing
    # the file means the GUI wheel is never fetched and the boot-time check stays satisfied.
    # Same version, only the distribution name changes; headless builds exist for every
    # version the variants pin (4.8.1.78, 5.0.0.93, 4.11.0.86 all verified on PyPI).
    # Any specifier form, not just `==`: a variant that moved to `opencv-python>=4.8`
    # would otherwise keep the GUI pin, and the sweep below would then uninstall a package
    # the requirements file still names — reopening the boot-time reinstall path silently.
    sed -i -E -e 's/^(opencv[-_](contrib[-_])?python)([-_]headless)?([[:space:]]*[=<>~!])/\1-headless\4/' -e 's/^(opencv[-_](contrib[-_])?python)([-_]headless)?[[:space:]]*$/\1-headless/' "$forge_req" && \
    uv pip install --no-cache-dir -r "$forge_req" && \
    # lllyasviel and reForge name no opencv at all and take it transitively, where the
    # pin rewrite above has nothing to bite on. Sweep whatever actually landed. Safe to
    # uninstall here precisely BECAUSE it is not in the requirements file: nothing at boot
    # resolves its metadata, so this cannot trigger the reinstall described above.
    for _cv in opencv-python opencv-contrib-python; do \
        _cvv="$(python -c "import importlib.metadata as m; print(m.version('${_cv}'))" 2>/dev/null || true)" && \
        [[ -n "${_cvv}" ]] || continue; \
        echo "Replacing transitively-installed ${_cv}==${_cvv} with the headless build" && \
        uv pip uninstall "${_cv}" && \
        uv pip install --no-cache-dir "${_cv}-headless==${_cvv}" || exit 1; \
    done && \
    # PROVE IT ON THE ARTIFACT. A headless install that resolved back to the GUI wheel
    # through some later dependency is invisible until a subprocess aborts at runtime, so
    # ask the installed cv2 what it actually is rather than trusting the install lines.
    python -c "import cv2, pathlib, sys; d = pathlib.Path(cv2.__file__).parent; sys.exit('FATAL: cv2 ships a Qt plugin tree at ' + str(d / 'qt') + ' — the GUI build aborts in a headless container (L090, ADR 0037)') if (d / 'qt').exists() else print('cv2 is headless:', cv2.__version__)" && \
    grep -qiE '^opencv[-_](contrib[-_])?python([[:space:]]*[=<>~!]|[[:space:]]*$)' "$forge_req" && \
        { echo "FATAL: $forge_req still pins a non-headless opencv — Forge would reinstall it on every container start (L090, ADR 0037)"; exit 1; } || true && \
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
