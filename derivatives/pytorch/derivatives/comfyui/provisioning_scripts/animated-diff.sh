#!/bin/bash
set -euo pipefail

# Install AnimateDiff-Evolved extension
cd /workspace/ComfyUI/custom_nodes || exit 1
if [ ! -d "ComfyUI-AnimateDiff-Evolved" ]; then
  git clone https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved.git
fi

MODEL_DIR="/workspace/ComfyUI/models/checkpoints"
mkdir -p "$MODEL_DIR"
cd "$MODEL_DIR"

# SD 2.1 768 checkpoint
if [ ! -f "v2-1_768-ema-pruned.safetensors" ]; then
  wget -O v2-1_768-ema-pruned.safetensors "https://huggingface.co/stabilityai/stable-diffusion-2-1/resolve/main/v2-1_768-ema-pruned.safetensors"
fi

if [ ! -f "v1-5-pruned-emaonly-fp16.safetensors" ]; then
  wget -O v1-5-pruned-emaonly-fp16.safetensors "https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly-fp16.safetensors"
fi

WORKFLOW_DIR="/workspace/ComfyUI/user/default/workflows"
mkdir -p "$WORKFLOW_DIR"
WORKFLOW_URL="https://gist.githubusercontent.com/chau-fifiwy/8e7a78adc49b391f3273c707e8394dac/raw/8ee39945f8764c7722e42679effe625ab0e3b06a/gistfile1.json"
WORKFLOW_PATH="$WORKFLOW_DIR/bunny_workflow.json"
if [ ! -f "$WORKFLOW_PATH" ]; then
  wget -O "$WORKFLOW_PATH" "$WORKFLOW_URL"
fi

echo "Provisioning complete!"
