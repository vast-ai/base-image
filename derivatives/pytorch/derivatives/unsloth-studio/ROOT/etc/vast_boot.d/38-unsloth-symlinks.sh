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
