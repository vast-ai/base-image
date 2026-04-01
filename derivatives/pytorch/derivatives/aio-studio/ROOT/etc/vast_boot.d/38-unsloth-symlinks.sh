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
    # Ensure llama.cpp points to the CUDA binaries (workspace sync may have
    # copied stale CPU-only binaries from a previous run)
    mkdir -p "${WORKSPACE}/unsloth/llama.cpp"
    ln -sfn /opt/llama.cpp-cuda "${WORKSPACE}/unsloth/llama.cpp/build/bin"
fi

# Register llama.cpp CUDA shared libs (must run every boot — the ldconfig
# conf may not survive image rebuilds or layer changes)
if [[ -d /opt/llama.cpp-cuda ]]; then
    echo "/opt/llama.cpp-cuda" > /etc/ld.so.conf.d/llama-cpp.conf
    ldconfig
fi
