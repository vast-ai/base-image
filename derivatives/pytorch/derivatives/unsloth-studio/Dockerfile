ARG PYTORCH_BASE=vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-06-15
FROM ${PYTORCH_BASE}

LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Unsloth Studio image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

COPY ./ROOT /

# Install CUDA dev libraries required by the llama.cpp CUDA build. The -mini base
# strips the CUDA math dev headers, so GGML_CUDA needs cuBLAS (cublas/cublasLt) and
# the CUDA runtime headers (cuda_runtime.h) back before nvcc can compile the .cu files.
# cuobjdump (not in -mini) is needed by the post-build arch-coverage assertion below.
ENV UV_TORCH_BACKEND=cu128
ARG PYTORCH_BASE
RUN \
    set -euo pipefail && \
    apt-get update && \
    apt-get install -y --no-install-recommends "libcublas-dev-12-9" "cuda-cudart-dev-12-9" "cuda-cuobjdump-12-9" && \
    ldconfig && \
    rm -rf /var/lib/apt/lists/* && \
    # Make CUDA driver stubs available for linking at build time (no GPU in Docker build)
    echo "/usr/local/cuda/lib64/stubs" > /etc/ld.so.conf.d/cuda-stubs.conf && \
    ldconfig

# Install unsloth, symlink the studio venv to /venv/main so setup.sh installs
# everything into our existing venv (avoids a duplicate torch install), then
# run studio setup for frontend build + llama.cpp + all python deps.
ARG UNSLOTH_VERSION=
RUN \
    set -euo pipefail && \
    . /opt/nvm/nvm.sh && \
    . /venv/main/bin/activate && \
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    unsloth_spec="unsloth${UNSLOTH_VERSION:+==${UNSLOTH_VERSION}}" && \
    uv pip install --no-cache-dir --torch-backend=cu128 "$unsloth_spec" && \
    # Store unsloth data under /opt so it survives volume mounts over /root
    mkdir -p /opt/workspace-internal/unsloth/studio && \
    ln -sf /opt/workspace-internal/unsloth /root/.unsloth && \
    # Symlink studio venv → /venv/main so setup.sh finds an existing venv
    ln -sf /venv/main /opt/workspace-internal/unsloth/studio/unsloth_studio && \
    # Patch install_python_stack.py to use cu128 torch backend instead of auto-detection
    find /venv/main/lib -name 'install_python_stack.py' -path '*/studio/*' \
        -exec sed -i 's|--torch-backend=auto|--torch-backend=cu128|' {} + && \
    # Patch setup.sh to build llama.cpp with portable CPU dispatch instead of -march=native.
    # Without this, the build host's AVX-512 leaks into libggml-cpu.so and SIGILLs on
    # any runtime CPU lacking AVX-512 (e.g. Zen 2). GGML_CPU_ALL_VARIANTS produces
    # per-microarch backends selected at runtime.
    find /venv/main/lib -name 'setup.sh' -path '*/studio/*' \
        -exec sed -i 's|-DGGML_NATIVE=ON|-DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=ON -DGGML_BACKEND_DL=ON -DBUILD_SHARED_LIBS=ON|' {} + && \
    # Force the CUDA llama.cpp build. setup.sh gates -DGGML_CUDA=ON on a RUNTIME GPU
    # probe (nvidia-smi -L / /proc/driver/nvidia/gpus) which is absent inside
    # `docker build`, so it silently falls through to a CPU-only binary (no
    # libggml-cuda.so) and every runtime inference runs on CPU. Provide a build-only
    # stub nvidia-smi so the probe passes, put nvcc on PATH, and pin the CUDA arch
    # list (the studio's UNSLOTH_LLAMA_CUDA_ARCHS override wins verbatim, so arch
    # selection does not depend on a real GPU). The stub is removed before the layer
    # ends so it never shadows the real runtime nvidia-smi. The stub answers ONLY
    # `-L` (the probe gate) and deliberately returns no compute_cap: if a future
    # studio release ignores UNSLOTH_LLAMA_CUDA_ARCHS, arch detection then finds no
    # capability and builds CPU-only, so the assertion below fails the build (fail
    # closed) instead of silently baking a narrow single-arch binary.
    printf '%s\n' '#!/bin/sh' 'case "$*" in' '  *-L*) echo "GPU 0: build-stub (UUID: GPU-00000000)";;' '  *) exit 0;;' 'esac' > /usr/local/bin/nvidia-smi && \
    chmod +x /usr/local/bin/nvidia-smi && \
    export PATH="/usr/local/cuda/bin:${PATH}" && \
    export CUDA_HOME="/usr/local/cuda" && \
    # Arches: Ampere(80) Ampere-cons(86) Ada(89) Hopper(90) Blackwell-dc(100) Blackwell-cons(120).
    # 100 is included because an sm_80 template floor also admits datacenter Blackwell
    # (sm_100 < sm_120), and the build emits SASS-only (no PTX) so an uncovered admitted
    # arch would crash at runtime (no-kernel-image), not offload to CPU.
    export UNSLOTH_LLAMA_CUDA_ARCHS="80;86;89;90;100;120" && \
    # Skip prebuilt llama.cpp (needs nvidia-smi), go straight to source build
    export UNSLOTH_LLAMA_FORCE_COMPILE=1 && \
    export SKIP_STUDIO_BASE=1 && \
    unsloth studio setup && \
    rm -f /usr/local/bin/nvidia-smi && \
    # Fail the build if the CUDA backend is missing (CPU-only fallback) OR was built
    # for the wrong arch set. The existence test guards the CPU-only regression; the
    # cuobjdump arch check guards a silent narrow/wrong-arch build that would crash at
    # runtime on an admitted GPU. Bracket the range: min (sm_80) and max (sm_120).
    test -f /opt/workspace-internal/unsloth/llama.cpp/build/bin/libggml-cuda.so || \
        { echo "FATAL: llama.cpp built WITHOUT the CUDA backend (libggml-cuda.so absent)"; exit 1; } && \
    for _a in sm_80 sm_120; do \
        cuobjdump --list-elf /opt/workspace-internal/unsloth/llama.cpp/build/bin/libggml-cuda.so | grep -q "$_a" || \
            { echo "FATAL: libggml-cuda.so has no $_a SASS — the CUDA arch list did not take effect"; exit 1; }; \
    done && \
    # Add llama.cpp shared libs to linker search path
    echo "/opt/workspace-internal/unsloth/llama.cpp/build/bin" > /etc/ld.so.conf.d/llama-cpp.conf && \
    ldconfig && \
    # Verify the torch ecosystem (torch + torchvision + torchaudio + torchcodec)
    # versions are unchanged.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
