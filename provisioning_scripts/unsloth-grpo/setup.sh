#!/bin/bash
# Sets up the GRPO recipe on Unsloth's official image (unsloth/unsloth) on Vast.
# The template's onstart sets GRPO_SRC (this file's pinned directory URL) and runs it, e.g.
#   GRPO_SRC=https://raw.githubusercontent.com/vast-ai/base-image/<commit>/provisioning_scripts/unsloth-grpo
# The image already provides the venv on PATH, HF_HOME and the notebook's python3 kernel.
set -euo pipefail

: "${GRPO_SRC:?GRPO_SRC is not set}"
GRPO_DIR=/workspace/grpo

# Recipe files; never overwrite a copy the user may have edited.
mkdir -p "$GRPO_DIR"
for f in train_grpo.py grpo_quickstart.ipynb; do
    [[ -f "$GRPO_DIR/$f" ]] && continue
    curl -fsSL --retry 3 -o "$GRPO_DIR/$f.part" "$GRPO_SRC/$f"
    mv "$GRPO_DIR/$f.part" "$GRPO_DIR/$f"
done

# GRPO_AUTORUN=1: train once per instance; the marker is outside /workspace so a reused
# volume does not suppress it. autorun.exit holds the script's real exit code.
if [[ "${GRPO_AUTORUN:-0}" == 1 && ! -f /.grpo-autorun-started ]]; then
    touch /.grpo-autorun-started
    cd "$GRPO_DIR"
    setsid nohup bash -c \
        '/opt/unsloth-venv/bin/python train_grpo.py ${GRPO_ARGS:-} > autorun.log 2>&1; echo $? > autorun.exit' \
        >/dev/null 2>&1 &
fi
