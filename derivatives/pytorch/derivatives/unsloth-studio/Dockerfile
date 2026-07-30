ARG PYTORCH_BASE=vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-06-15
FROM ${PYTORCH_BASE}

LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Unsloth Studio image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

COPY ./ROOT /

# CUDA libraries the prebuilt llama.cpp links against. The -mini base strips these, and
# the bundle ships no CUDA of its own (NEEDED: libcudart.so.12, libcublas.so.12), so they
# must be present or the CUDA backend is skipped at dlopen and inference runs on CPU.
# These are the RUNTIME packages: ADR 0018 replaced the source build with a prebuilt
# bundle, so no nvcc and no dev headers are needed. cuobjdump (not in -mini) is a
# standalone package and stays — it powers the arch-coverage assertion (L057).
ENV UV_TORCH_BACKEND=cu128
ARG PYTORCH_BASE
RUN \
    set -euo pipefail && \
    apt-get update && \
    apt-get install -y --no-install-recommends "libcublas-12-9" "cuda-cudart-12-9" "cuda-cuobjdump-12-9" && \
    rm -rf /var/lib/apt/lists/* && \
    ldconfig

# Install the prebuilt llama.cpp bundle published by the Unsloth fork (ADR 0018).
#
# The three ARGs are resolved by the CI preflight job — release tag, exact asset name,
# and expected sha256, looked up in that release's llama-prebuilt-manifest.json /
# llama-prebuilt-sha256.json on install_kind=linux-cuda + coverage_class=portable +
# the runtime line matching this base's CUDA major. Selection lives in CI so this layer
# fetches ONE known URL and verifies ONE known hash; it never constructs an asset name
# or trusts a manifest at build time. All three are required: an unset ARG fails the
# build rather than silently falling back.
#
# The tree lives at /opt/llama-cpp — deliberately OUTSIDE /opt/workspace-internal, so the
# boot-time workspace sync cannot shadow image content with a stale copy from a reused
# volume, and deliberately WITHOUT "cuda" in the path, because the base CUDA boot hook
# strips ld.so.conf.d lines matching "cuda". The bundle's binaries carry RUNPATH $ORIGIN,
# so they resolve their own libs and need no entry on the container-wide linker path.
ARG LLAMA_RELEASE=
ARG LLAMA_ASSET=
ARG LLAMA_SHA256=
RUN \
    set -euo pipefail && \
    for _v in LLAMA_RELEASE LLAMA_ASSET LLAMA_SHA256; do \
        [[ -n "${!_v:-}" ]] || { echo "FATAL: build arg ${_v} is required (resolved by the CI preflight job)"; exit 1; }; \
    done && \
    _tmp="$(mktemp -d)" && \
    curl -fsSL --retry 5 --retry-delay 2 --retry-all-errors --max-time 900 \
        -o "${_tmp}/${LLAMA_ASSET}" \
        "https://github.com/unslothai/llama.cpp/releases/download/${LLAMA_RELEASE}/${LLAMA_ASSET}" && \
    echo "${LLAMA_SHA256}  ${_tmp}/${LLAMA_ASSET}" | sha256sum -c - && \
    mkdir -p /opt/llama-cpp/build/bin && \
    # Extract with Python's `data` filter, not `tar xf`: it refuses absolute paths, `..`
    # traversal, links pointing outside the destination, device nodes and setuid bits.
    # We fetch this archive ourselves instead of going through the studio's installer,
    # so we owe the same extraction hardening it does (ADR 0018 binding condition).
    python3 -c "\
import sys, tarfile;\
src, dest = sys.argv[1], sys.argv[2];\
tarfile.open(src).extractall(dest, filter='data')" "${_tmp}/${LLAMA_ASSET}" /opt/llama-cpp/build/bin && \
    rm -rf "${_tmp}" && \
    # Metadata belongs at the install root, where the studio looks for it; the binaries
    # stay under build/bin, which is the layout both the studio's installer and its
    # source build produce. `if/then` rather than `&& mv || true`: the latter swallows a
    # failed mv, because the `|| true` makes the whole loop body succeed regardless.
    for _m in UNSLOTH_PREBUILT_INFO.json BUILD_INFO.txt THIRD_PARTY_LICENSES.txt; do \
        if [[ -f "/opt/llama-cpp/build/bin/${_m}" ]]; then mv "/opt/llama-cpp/build/bin/${_m}" /opt/llama-cpp/; fi; \
    done && \
    test -x /opt/llama-cpp/build/bin/llama-server || \
        { echo "FATAL: bundle ${LLAMA_ASSET} contains no build/bin/llama-server"; exit 1; } && \
    # MIT requires the notice to accompany binary redistribution. The bundle carries it
    # inside THIRD_PARTY_LICENSES.txt (there is no separate LICENSE file), so assert the
    # notice we actually ship rather than trusting a future bundle to keep it.
    test -f /opt/llama-cpp/THIRD_PARTY_LICENSES.txt || \
        { echo "FATAL: bundle ${LLAMA_ASSET} ships no third-party licence notice"; exit 1; } && \
    # Fingerprint the binary we just verified by hash, so the post-setup check can prove
    # the tree was REUSED rather than replaced. `unsloth studio setup` writes an
    # identically-named marker on its own prebuilt path, so the marker's presence alone
    # cannot distinguish our verified artifact from one it downloaded itself.
    sha256sum /opt/llama-cpp/build/bin/llama-server > /opt/llama-cpp.server.sha256

# Install unsloth, symlink the studio venv to /venv/main so setup.sh installs
# everything into our existing venv (avoids a duplicate torch install), then run studio
# setup for the frontend build + all python deps. llama.cpp is NOT built here: setup.sh
# is pointed at the verified bundle above and reuses it.
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
    # Point setup.sh at the verified bundle. Because this path differs from the canonical
    # install dir, setup.sh symlinks the canonical dir at it and sets
    # _NEED_LLAMA_SOURCE_BUILD=false + _SKIP_PREBUILT_INSTALL=true, so it neither
    # downloads (its resolver needs a real driver, absent here) nor compiles.
    # Fail-closed by construction: if a future studio release drops that reuse branch,
    # setup falls through to its own prebuilt path (unavailable without a driver) and
    # then to a source build with no nvcc on PATH — which produces no libggml-cuda.so,
    # so the assertions below fail the build instead of shipping a CPU-only binary.
    export UNSLOTH_LOCAL_LLAMA_CPP_DIR=/opt/llama-cpp && \
    export SKIP_STUDIO_BASE=1 && \
    unsloth studio setup && \
    # Prove the reuse branch actually fired — do not infer it. Two independent facts:
    # the canonical install path resolves to our tree, AND the binary is byte-identical
    # to the one whose sha256 we verified before setup ran. The marker file alone proves
    # nothing here: the studio's own installer writes an identically-named marker, and
    # because the canonical path is a symlink INTO our tree, a re-download would land on
    # top of it and still satisfy both the readlink and the marker checks.
    [[ "$(readlink -f /opt/workspace-internal/unsloth/llama.cpp)" == "/opt/llama-cpp" ]] || \
        { echo "FATAL: studio setup did not reuse /opt/llama-cpp — it built or downloaded its own llama.cpp"; exit 1; } && \
    sha256sum -c /opt/llama-cpp.server.sha256 >/dev/null || \
        { echo "FATAL: llama-server changed during studio setup — the verified bundle was replaced"; exit 1; } && \
    rm -f /opt/llama-cpp.server.sha256 && \
    test -f /opt/llama-cpp/UNSLOTH_PREBUILT_INFO.json || \
        { echo "FATAL: prebuilt marker missing from /opt/llama-cpp"; exit 1; } && \
    # The binary must actually execute. The fork links llama-server against OpenSSL
    # (libssl/libcrypto), which the source build never needed, so `test -x` above can
    # pass on a binary that dies at load time with an unresolved library. This also puts
    # the real build number in the build log.
    /opt/llama-cpp/build/bin/llama-server --version || \
        { echo "FATAL: llama-server does not execute — its link closure is incomplete"; exit 1; } && \
    # The -mini base ships the CUDA toolchain OFF PATH, so cuobjdump (installed above)
    # is not resolvable by name without this. It was previously on PATH only as a side
    # effect of the nvcc export the source build needed.
    export PATH="/usr/local/cuda/bin:${PATH}" && \
    # Fail the build unless the CUDA backend EXISTS, RESOLVES and COVERS the arch range.
    # Existence alone is satisfied by `tar x` and proves nothing now that the binary is
    # not compiled here (L056/ADR 0018).
    test -f /opt/llama-cpp/build/bin/libggml-cuda.so || \
        { echo "FATAL: bundle has no CUDA backend (libggml-cuda.so absent)"; exit 1; } && \
    # llama.cpp is built with GGML_BACKEND_DL, so a backend whose NEEDED libs are missing
    # is SKIPPED at dlopen and every inference silently runs on CPU. libcuda.so.1 is the
    # driver, injected by the container runtime on a real host, so it is the only
    # permitted miss here.
    # NOTE: this resolves under the BUILD-time linker config. At boot,
    # 05-configure-cuda.sh deletes /etc/ld.so.conf.d/*cuda*.conf and re-adds only
    # /usr/local/cuda/lib64 — so this assertion holds at runtime because the CUDA runtime
    # packages install under /usr/local/cuda-12.9/lib64, which that path resolves to.
    # That coupling is load-bearing: a CUDA lib landing elsewhere would pass here and
    # fail on the instance.
    _ldd="$(ldd -r /opt/llama-cpp/build/bin/libggml-cuda.so 2>&1 || true)" && \
    _missing="$(printf '%s\n' "$_ldd" | grep 'not found' | grep -v 'libcuda\.so\.1' || true)" && \
    [[ -z "$_missing" ]] || \
        { echo "FATAL: libggml-cuda.so has unresolved libraries: $_missing"; exit 1; } && \
    # Read the arch coverage out of the ARTIFACT, never out of the bundle's declared
    # supported_sms: that manifest has been observed to overstate coverage (it claims
    # sm_103, which ships no SASS). Capture the cubin list ONCE then match in-shell — do
    # NOT pipe into `grep -q`: under `set -o pipefail` grep's early exit SIGPIPEs
    # cuobjdump (rc 141) and the pipeline reports failure even on a match.
    # ENUMERATE every admitted arch, do not bracket: nothing upstream guarantees that
    # covering the ends covers the middle, and this very bundle proves the declared set
    # can be wrong. A bundle silently dropping sm_90 would otherwise ship green and give
    # H100 renters a no-kernel-image crash. sm_75 is asserted ahead of the floor move to
    # 750, so that change is a template-only step; sm_103 is deliberately absent (the
    # bundle ships no sm_103 SASS and reaches it by JIT from PTX).
    _elf="$(cuobjdump --list-elf /opt/llama-cpp/build/bin/libggml-cuda.so)" && \
    for _a in sm_75 sm_80 sm_86 sm_89 sm_90 sm_100 sm_120; do \
        [[ "$_elf" == *"$_a"* ]] || \
            { echo "FATAL: libggml-cuda.so has no $_a SASS — bundle ${LLAMA_ASSET} does not cover the admitted GPU range"; exit 1; }; \
    done && \
    # Record (do not gate on) the PTX set: PTX is what lets an arch above the SASS range
    # JIT instead of crashing, so it belongs in the build log for the floor discussion.
    echo "llama.cpp PTX coverage: $(cuobjdump --list-ptx /opt/llama-cpp/build/bin/libggml-cuda.so 2>/dev/null | grep -oE 'sm_[0-9]+' | sort -u | tr '\n' ' ')" && \
    # Verify the torch ecosystem (torch + torchvision + torchaudio + torchcodec)
    # versions are unchanged. The comparison is BRACED so its `||` binds only to the
    # test: unbraced, it catches a failure anywhere earlier in this `&&` chain and
    # reports it as "torch drift" — which is exactly how a missing cuobjdump once
    # masqueraded as a version drift, complete with an unbound-variable error.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    { [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }; }

# Record which llama.cpp release was baked in, so the shipped version is answerable from
# `docker inspect` (and from inside the instance) without booting and running the binary.
# ADR 0018 calls for a label; L001 pins the Dockerfile to exactly three LABEL keys, so
# this is an ENV instead — same visibility, and readable at runtime by the agent guide.
ENV LLAMA_CPP_RELEASE=${LLAMA_RELEASE}

# Defend against environment clashes when syncing to volume
RUN env-hash > /.env_hash
