#!/bin/bash

# Environment variables to be used by ollama serve and for terminal operation

# Do not require this to be set in the template unless user wants an override
if [[ -z $PORTAL_CONFIG ]]; then
    export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:11434:21434:/:Ollama API|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal"
fi

# OLLAMA_MODEL takes precedence over serverless convention if set
export MODEL_NAME="${OLLAMA_MODEL:-$MODEL_NAME}"
export OLLAMA_MODEL="$MODEL_NAME"

# Bind to internal port (Caddy proxies external 11434 -> internal 21434)
export OLLAMA_HOST=${OLLAMA_HOST:-0.0.0.0:21434}
export OLLAMA_PORT=${OLLAMA_HOST##*:}

# Persistent model storage in workspace
export OLLAMA_MODELS="${OLLAMA_MODELS:-${WORKSPACE:-/workspace}/ollama/models}"
mkdir -p "${OLLAMA_MODELS}"
