#!/bin/bash

# Default portal configuration for AIO Studio
if [[ -z $PORTAL_CONFIG ]]; then
    export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:6100:16100:/:Desktop|localhost:8188:18188:/:ComfyUI|localhost:7860:17860:/:SD Forge|localhost:18675:8675:/:AI Toolkit|localhost:13000:3000:/:ACE Step|localhost:8888:18888:/:Unsloth Studio|localhost:7493:17493:/:Voicebox|localhost:17862:7862:/:Whisper WebUI|localhost:18000:8000:/:Whisper API|localhost:7861:7861:/:Wan2GP|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal|localhost:8384:18384:/:Syncthing"
fi

# Unsloth environment
export UV_TORCH_BACKEND=cu128
