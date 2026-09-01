ARG PYTORCH_BASE=vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-09-01
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

# llama.cpp comes from the Unsloth fork's PREBUILT portable bundle, not a source build
# (ADR 0018, using the machinery ADR 0033 proved on the llama-cpp image).
#
# What the source build cost: a 20-40 minute CUDA compile on every image build; a
# FABRICATED nvidia-smi stub, because setup.sh gates -DGGML_CUDA=ON on a runtime GPU
# probe that `docker build` cannot satisfy, and without the stub it silently produced a
# CPU-only binary (ADR 0016); a hand-pinned arch list with no sm_75, which blocks
# lowering the template floor to Turing; and a binary that could not report its own
# version, because a --depth 1 clone breaks build-info.cmake and llama-server answered
# `version: 1`.
#
# Same artifact family as derivatives/llama-cpp: app-<ref>-linux-x64-cuda12-portable.
# The CUDA-12 line is the one that matches this image's cu128 torch, and the image is
# amd64-only, so there is exactly one asset — no matrix, unlike llama-cpp's two lines.
#
# Selection is by NAME and verification is by HASH, exactly as llama-cpp does it. The
# earlier design resolved the asset through the release's own manifest inside a
# composite action; naming it directly is less machinery for the same result, and it
# reuses a path that has now shipped and promoted rather than a second one.
# LLAMA_CPP_VERSION carries no default ON PURPOSE. It is RESOLVED from the publisher in
# CI (check-github-release against unslothai/llama.cpp, tag-pattern '-mix-') and passed
# in; a default here would be a second, silently-stale source of truth for the same fact,
# and a plain `docker build` should fail loudly rather than bake whatever version was
# current when this file was last edited.
ARG LLAMA_CPP_VERSION
ARG LLAMA_BUNDLE=cuda12-portable
ENV LLAMA_CPP_RELEASE=${LLAMA_CPP_VERSION}
RUN \
    set -euo pipefail && \
    [[ -n "${LLAMA_CPP_VERSION}" ]] || { echo "Must specify LLAMA_CPP_VERSION"; exit 1; } && \
    # The arch slug is unsloth's, not dpkg's — amd64 is spelled `x64` there. This image
    # is amd64-only, but map rather than hardcode so an arm64 build fails loudly here
    # instead of 404ing on a URL that silently means something else.
    case "$(dpkg --print-architecture)" in \
        amd64) arch_slug=x64 ;; \
        *) echo "unsloth-studio is amd64-only; got $(dpkg --print-architecture)"; exit 1 ;; \
    esac && \
    package_name="app-${LLAMA_CPP_VERSION}-linux-${arch_slug}-${LLAMA_BUNDLE}.tar.gz" && \
    base_url="https://github.com/unslothai/llama.cpp/releases/download/${LLAMA_CPP_VERSION}" && \
    mkdir -p /opt/llama-cpp/build/bin /tmp/llama-dl && \
    wget -O "/tmp/llama-dl/${package_name}" "${base_url}/${package_name}" && \
    wget -O /tmp/llama-dl/sha256.json "${base_url}/llama-prebuilt-sha256.json" && \
    # VERIFY BEFORE EXTRACT, and fail when the manifest has no entry for OUR asset
    # rather than comparing empty-to-empty — the sha256 of no input is a constant, so
    # two failed lookups compare equal and the check passes having verified nothing.
    # Reach, stated honestly: the hashes are published by the same party as the asset,
    # so this detects transport damage, not a compromised publishing path.
    python3 -c "\
import json,hashlib,sys;\
name=sys.argv[1];\
m=json.load(open('/tmp/llama-dl/sha256.json')).get('artifacts',{});\
e=m.get(name) or sys.exit('no sha256 entry for '+name+' in llama-prebuilt-sha256.json');\
want=e.get('sha256') or sys.exit('empty sha256 entry for '+name);\
h=hashlib.sha256(open('/tmp/llama-dl/'+name,'rb').read()).hexdigest();\
sys.exit(0) if h==want else sys.exit('sha256 mismatch for '+name+': got '+h+' want '+want)\
" "${package_name}" && \
    # Extract with Python's `data` filter rather than `tar xf`: it refuses absolute
    # paths, `..` traversal, links escaping the destination, and setuid bits. The
    # studio's own installer applies that filter when IT unpacks this bundle, and we
    # bypass that installer, so we owe the same hardening.
    python3 -c "\
import sys, tarfile;\
src, dest = sys.argv[1], sys.argv[2];\
tarfile.open(src).extractall(dest, filter='data')" \
        "/tmp/llama-dl/${package_name}" /opt/llama-cpp/build/bin && \
    rm -rf /tmp/llama-dl && \
    # The bundle is FLAT, but the studio installer expects a llama.cpp source-tree shape
    # with build/bin/. Extract into build/bin and lift the tree-level metadata up, so the
    # binaries stay where both the installer and its runtime callers look for them.
    for _m in UNSLOTH_PREBUILT_INFO.json THIRD_PARTY_LICENSES.txt; do \
        if [[ -f "/opt/llama-cpp/build/bin/${_m}" ]]; then mv "/opt/llama-cpp/build/bin/${_m}" /opt/llama-cpp/; fi; \
    done && \
    test -x /opt/llama-cpp/build/bin/llama-server || \
        { echo "FATAL: bundle ${package_name} contains no build/bin/llama-server"; exit 1; } && \
    test -f /opt/llama-cpp/build/bin/libggml-cuda.so || \
        { echo "FATAL: bundle ${package_name} ships no CUDA backend"; exit 1; } && \
    # MIT requires the notice to accompany binary redistribution. The bundle carries it
    # in THIRD_PARTY_LICENSES.txt (there is no separate LICENSE file), so assert the
    # notice we actually ship rather than trusting a future bundle to keep it.
    test -f /opt/llama-cpp/THIRD_PARTY_LICENSES.txt || \
        { echo "FATAL: bundle ${package_name} ships no third-party licence notice"; exit 1; } && \
    # Fingerprint the verified binary so the post-setup check can prove the tree was
    # REUSED rather than replaced. The marker file alone cannot: `unsloth studio setup`
    # writes an identically-named marker on its own prebuilt path.
    sha256sum /opt/llama-cpp/build/bin/llama-server > /opt/llama-cpp.server.sha256

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
    # the one whose sha256 was verified before setup ran. Neither alone is enough: the
    # canonical path is a symlink INTO our tree, so a re-download would land on top of
    # it and still satisfy the readlink and the marker.
    [[ "$(readlink -f /opt/workspace-internal/unsloth/llama.cpp)" == "/opt/llama-cpp" ]] || \
        { echo "FATAL: studio setup did not reuse /opt/llama-cpp — it built or downloaded its own llama.cpp"; exit 1; } && \
    sha256sum -c /opt/llama-cpp.server.sha256 >/dev/null || \
        { echo "FATAL: llama-server changed during studio setup — the verified bundle was replaced"; exit 1; } && \
    rm -f /opt/llama-cpp.server.sha256 && \
    # The binary must EXECUTE. The fork links llama-server against OpenSSL, which the
    # source build never needed, so `test -x` can pass on a binary that dies at load
    # time with an unresolved library. This also puts the real build number in the log,
    # which the source build could never report (--depth 1 broke build-info.cmake and
    # llama-server answered `version: 1`).
    /opt/llama-cpp/build/bin/llama-server --version || \
        { echo "FATAL: llama-server does not execute — its link closure is incomplete"; exit 1; } && \
    # The CUDA backend must exist AND RESOLVE. Existence alone is now satisfied by
    # `tar x` and proves nothing: llama.cpp is built GGML_BACKEND_DL=ON, so a
    # libggml-cuda.so whose dlopen fails is skipped silently and every inference runs
    # on CPU — ADR 0016's defect, moved from build time to runtime. Gated by L056.
    export PATH="/usr/local/cuda/bin:${PATH}" && \
    ldd -r /opt/llama-cpp/build/bin/libggml-cuda.so > /tmp/ggml-cuda.ldd 2>&1; \
    if grep -qE "not found|undefined symbol" /tmp/ggml-cuda.ldd; then \
        echo "FATAL: libggml-cuda.so does not resolve:"; cat /tmp/ggml-cuda.ldd; exit 1; \
    fi && \
    rm -f /tmp/ggml-cuda.ldd && \
    # SASS coverage of the shipped artifact, read from the BINARY rather than from the
    # bundle's own manifest — measured on a real release, the manifest claimed sm_103
    # the artifact does not contain. An admitted GPU with no kernel image CRASHES
    # (no-kernel-image); it does not fall back. Bracket the template floor: sm_75
    # (Turing, the floor this bundle finally makes reachable) and sm_120 (consumer
    # Blackwell). Capture ONCE then match in-shell — piping into `grep -q` SIGPIPEs
    # cuobjdump under pipefail and reports failure on a match. Gated by L081.
    _elf="$(cuobjdump --list-elf /opt/llama-cpp/build/bin/libggml-cuda.so)" && \
    for _a in sm_75 sm_120; do \
        [[ "$_elf" == *"$_a"* ]] || \
            { echo "FATAL: libggml-cuda.so has no $_a SASS — wrong bundle for this image's GPU floor"; exit 1; }; \
    done && \
    # Verify the torch ecosystem (torch + torchvision + torchaudio + torchcodec)
    # versions are unchanged.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
