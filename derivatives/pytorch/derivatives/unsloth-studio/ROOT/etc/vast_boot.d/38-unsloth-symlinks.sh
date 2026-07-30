#!/bin/bash

# Fix unsloth data symlinks after workspace sync (36-sync-workspace.sh).
# At build time, ~/.unsloth → /opt/workspace-internal/unsloth.  The workspace
# sync copies that directory to $WORKSPACE/unsloth, so the original directory
# and the symlink both become stale.  Replace them with symlinks to the runtime
# location so that paths baked into the venv (e.g. studio/unsloth_studio →
# /venv/main) resolve correctly from either location.
WORKSPACE="${WORKSPACE:-/workspace}"
if [[ -d "${WORKSPACE}/unsloth" ]]; then
    rm -rf /opt/workspace-internal/unsloth
    ln -sfn "${WORKSPACE}/unsloth" /opt/workspace-internal/unsloth
    ln -sfn "${WORKSPACE}/unsloth" /root/.unsloth
fi

# llama.cpp is IMAGE content, not user data (ADR 0018).  The workspace sync only copies
# when the target is absent, so an instance started against a volume that already has a
# $WORKSPACE/unsloth from an older image would otherwise keep running that image's
# llama.cpp — including a build predating the CUDA fix — no matter what this image
# contains, and the version marker would describe the wrong binary.  Repoint the studio's
# install path at the copy baked into THIS image on every boot.  Only the build tree is
# replaced; datasets, runs and exports elsewhere under $WORKSPACE/unsloth are untouched.
if [[ -d /opt/llama-cpp && -d "${WORKSPACE}/unsloth" ]]; then
    if [[ "$(readlink -f "${WORKSPACE}/unsloth/llama.cpp" 2>/dev/null)" != "/opt/llama-cpp" ]]; then
        # Say so in the boot log: this removes a multi-GB directory on a persistent
        # volume, and it is the one action here a support question would ask about.
        if [[ -e "${WORKSPACE}/unsloth/llama.cpp" ]]; then
            echo "38-unsloth-symlinks: replacing stale ${WORKSPACE}/unsloth/llama.cpp with the llama.cpp baked into this image (${LLAMA_CPP_RELEASE:-unknown})"
        fi
        rm -rf "${WORKSPACE}/unsloth/llama.cpp"
        ln -sfn /opt/llama-cpp "${WORKSPACE}/unsloth/llama.cpp"
    fi
fi
