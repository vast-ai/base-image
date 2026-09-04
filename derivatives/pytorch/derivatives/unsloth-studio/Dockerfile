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
        "/tmp/llama-dl/${package_name}" /opt/llama-cpp && \
    rm -rf /tmp/llama-dl && \
    # EXTRACT FLAT, THEN MIRROR INTO build/bin. The bundle IS flat, and its two
    # consumers disagree about where the binaries live:
    #
    #   studio setup.sh              expects a source-tree shape, build/bin/
    #   unsloth_zoo check_llama_cpp  searches ONLY the folder ROOT on Linux
    #                                (search_dirs = [llama_cpp_folder]; build/bin is
    #                                consulted on Windows and nowhere else)
    #
    # ADR 0018 extracted into build/bin/ for the first and left the root holding two
    # marker files. That satisfied the installer and broke the exporter: on 2026-09-04 a
    # GGUF export found no llama-quantize at the root, concluded llama.cpp was missing,
    # and installed its OWN prebuilt — which resolved to the CPU bundle and was laid over
    # the root, leaving the image with two llama.cpp installs and a 24-byte
    # llama-quantize stub. Inference kept using build/bin and stayed on the GPU, which is
    # why the downgrade stayed invisible until an export was attempted, long after every
    # build assertion and QA cell had passed.
    #
    # Root is now the real install, matching the bundle's own shape, so the exporter finds
    # it and never reinstalls. build/bin is populated by HARDLINK — same inodes, so no
    # second copy of a 350 MB backend — and setup.sh's expected shape is intact.
    #
    # WHY HARDLINKS ARE SAFE HERE, given they cannot cross a filesystem:
    #
    #  1. Both ends are inside /opt/llama-cpp, created and linked in THIS single RUN
    #     layer. There is no mount boundary between a directory and its own child, so
    #     EXDEV is not reachable at build time.
    #
    #  2. $WORKSPACE — which IS a volume on many instances — never receives these files.
    #     The boot sync (36-sync-workspace.sh) copies /opt/workspace-internal with
    #     `cp -ru`, and the llama.cpp entry there is a SYMLINK to /opt/llama-cpp, not a
    #     copy of it. `cp -ru` preserves symlinks rather than dereferencing them, so the
    #     volume gets a 14-byte link and the binaries stay in the image. Verified on a
    #     live instance 2026-09-04:
    #         /workspace/unsloth/llama.cpp -> /opt/llama-cpp
    #     So no hardlink is ever copied ACROSS the image/volume boundary, and mounting a
    #     volume at $WORKSPACE cannot split the linked pair or duplicate the 350 MB.
    #
    #  3. `find -exec` does NOT propagate the exit status of the command it runs — a
    #     failing `ln` would leave find returning 0 and the build continuing. That is why
    #     the build/bin assertions below are separate and explicit: they are what turns a
    #     silently-missing mirror into a failed build.
    #  4. `cp -al`, not `find -type f -exec ln`. The first attempt used the latter and
    #     the build failed on `llama-server does not execute`: the binaries carry
    #     RUNPATH=$ORIGIN, so they resolve libraries NEXT TO THEMSELVES, and `-type f`
    #     silently skipped the five versioned .so SYMLINKS (libggml.so.0 ->
    #     libggml.so.0.22.0 and friends). The mirror had 56 regular files and none of the
    #     links, so ldd reported five `not found` and the binary would not start from
    #     build/bin. `cp -al` hardlinks regular files AND recreates symlinks as symlinks,
    #     which is what $ORIGIN resolution needs. The bundle ships its own `build/`, so
    #     that entry is excluded rather than recursed into.
    mkdir -p /opt/llama-cpp/build/bin && \
    find /opt/llama-cpp -maxdepth 1 -mindepth 1 ! -name build \
        -exec cp -al -t /opt/llama-cpp/build/bin/ {} + && \
    # THE CUDA BUNDLE IS BINARIES ONLY — no convert_hf_to_gguf.py, no gguf-py (verified
    # against the real artifact: zero .py files). check_llama_cpp requires a quantizer AND
    # a converter, so with the bundle alone it can never pass, whatever the layout, and the
    # exporter reinstalls every time. That is what actually happened on 2026-09-04: the
    # reinstall resolved to the -cpu bundle (which DOES carry the converter) and laid it
    # over our tree.
    #
    # So take the converter from the SAME source tag the binaries were built from, rather
    # than letting the studio fetch a whole second bundle to get it. Pure Python, no build,
    # and pinned to ${LLAMA_CPP_VERSION} so the two halves cannot drift apart.
    _src="https://raw.githubusercontent.com/unslothai/llama.cpp/${LLAMA_CPP_VERSION}" && \
    wget -qO /opt/llama-cpp/convert_hf_to_gguf.py "${_src}/convert_hf_to_gguf.py" && \
    mkdir -p /opt/llama-cpp/gguf-py && \
    python3 -c "\
import io,sys,tarfile,urllib.request;\
v=sys.argv[1];\
u='https://codeload.github.com/unslothai/llama.cpp/tar.gz/refs/tags/'+v;\
t=tarfile.open(fileobj=io.BytesIO(urllib.request.urlopen(u, timeout=180).read()));\
ms=[m for m in t.getmembers() if '/gguf-py/' in m.name];\
sys.exit('no gguf-py in source tag '+v) if not ms else None;\
root=ms[0].name.split('/gguf-py/')[0]+'/gguf-py/';\
[setattr(m,'name',m.name[len(root):]) for m in ms];\
t.extractall('/opt/llama-cpp/gguf-py', members=[m for m in ms if m.name], filter='data')\
" "${LLAMA_CPP_VERSION}" && \
    # BOTH layouts must hold. Asserted separately so a regression names which consumer it
    # broke, rather than failing on a generic "llama-server missing".
    test -x /opt/llama-cpp/llama-quantize || \
        { echo "FATAL: ${package_name} leaves no llama-quantize at the ROOT — check_llama_cpp would reinstall over it (ADR 0036)"; exit 1; } && \
    test -f /opt/llama-cpp/convert_hf_to_gguf.py || \
        { echo "FATAL: convert_hf_to_gguf.py did not land from source tag ${LLAMA_CPP_VERSION} — GGUF export would reinstall llama.cpp"; exit 1; } && \
    test -f /opt/llama-cpp/gguf-py/gguf/__init__.py || \
        { echo "FATAL: gguf-py did not land from source tag ${LLAMA_CPP_VERSION} — the converter cannot import gguf"; exit 1; } && \
    # Prove the converter is USABLE, not merely present: import gguf out of the tree we
    # just assembled. A file that exists but cannot import leaves the exporter in exactly
    # the state this change exists to prevent.
    #
    # Uses /venv/main's interpreter, not the bare `python3` on PATH: gguf imports numpy,
    # which the venv has (torch depends on it) and the system python may not. Asserting
    # with an interpreter the exporter never uses would prove the wrong thing — and would
    # fail this build for a missing numpy rather than a missing converter.
    PYTHONPATH=/opt/llama-cpp/gguf-py /venv/main/bin/python -c \
        "import gguf; print('gguf importable from the pinned tree:', gguf.__file__)" && \
    test -x /opt/llama-cpp/llama-server || \
        { echo "FATAL: ${package_name} contains no llama-server at the root"; exit 1; } && \
    test -x /opt/llama-cpp/build/bin/llama-server || \
        { echo "FATAL: build/bin mirror lacks llama-server — studio setup.sh will not find it"; exit 1; } && \
    # PRESENCE IS NOT ENOUGH, and this is the assertion the first attempt was missing.
    # RUNPATH=$ORIGIN means each copy resolves libraries beside itself, so a mirror can be
    # complete in filenames and still not start. Run BOTH; --version is the cheapest call
    # that forces the full link closure.
    /opt/llama-cpp/llama-server --version >/dev/null || \
        { echo "FATAL: root llama-server does not execute — link closure incomplete"; exit 1; } && \
    /opt/llama-cpp/build/bin/llama-server --version >/dev/null || \
        { echo "FATAL: mirrored llama-server does not execute — the mirror is missing libraries it resolves via RUNPATH=\$ORIGIN"; exit 1; } && \
    test -f /opt/llama-cpp/libggml-cuda.so || \
        { echo "FATAL: ${package_name} ships no CUDA backend"; exit 1; } && \
    test -f /opt/llama-cpp/build/bin/libggml-cuda.so || \
        { echo "FATAL: build/bin mirror lacks libggml-cuda.so"; exit 1; } && \
    # MIT requires the notice to accompany binary redistribution. The bundle carries it
    # in THIRD_PARTY_LICENSES.txt (there is no separate LICENSE file), so assert the
    # notice we actually ship rather than trusting a future bundle to keep it.
    test -f /opt/llama-cpp/THIRD_PARTY_LICENSES.txt || \
        { echo "FATAL: bundle ${package_name} ships no third-party licence notice"; exit 1; } && \
    # Fingerprint the verified binary so the post-setup check can prove the tree was
    # REUSED rather than replaced. The marker file alone cannot: `unsloth studio setup`
    # writes an identically-named marker on its own prebuilt path.
    # Fingerprint the ROOT binary: it is the authoritative install now, and the
    # build/bin mirror is a hardlink of the same inode, so this covers both.
    sha256sum /opt/llama-cpp/llama-server > /opt/llama-cpp.server.sha256

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
