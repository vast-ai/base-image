#!/bin/bash

# Environment variables for llama-server and terminal operation

# Default portal configuration
if [[ -z $PORTAL_CONFIG ]]; then
    export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:8000:18000:/:Llama.cpp UI|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal"
fi

# LLAMA_MODEL takes precedence over serverless convention if set
export MODEL_NAME="${LLAMA_MODEL:-$MODEL_NAME}"
export LLAMA_MODEL="$MODEL_NAME"

# Set up cache directory symlink in workspace
llama_cache="${WORKSPACE:-/workspace}/llama.cpp"
mkdir -p "${llama_cache}"
default_cache="${HOME}/.cache/llama.cpp"
mkdir -p "${HOME}/.cache"
ln -sf "${llama_cache}" "${default_cache}" 2>/dev/null || true
