#!/bin/bash

# Environment variables to be used by ollama serve and for terminal operation

# Do not require this to be set in the template unless user wants an override
if [[ -z $PORTAL_CONFIG ]]; then
    export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:7860:17860:/:Model UI|localhost:11434:21434:/:Ollama API|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal"
fi

# OLLAMA_MODEL takes precedence over serverless convention if set
export MODEL_NAME="${OLLAMA_MODEL:-$MODEL_NAME}"
export OLLAMA_MODEL="$MODEL_NAME"

# Bind the internal port to loopback ONLY. External access is via the authenticated
# Caddy edge, which proxies external 11434 -> internal 21434 over localhost.
# NOTE: the upstream image bakes `ENV OLLAMA_HOST=0.0.0.0:11434`, so this `:-` default
# would never fire on its own — the Dockerfile resets ENV OLLAMA_HOST=127.0.0.1:21434
# to override it (see the SECURITY note there). This line keeps loopback as the value of
# record and as a fallback. Never let Ollama bind 0.0.0.0: combined with the upstream
# `EXPOSE 11434` it leaves the API reachable unauthenticated, bypassing Caddy token auth.
export OLLAMA_HOST=${OLLAMA_HOST:-127.0.0.1:21434}
export OLLAMA_PORT=${OLLAMA_HOST##*:}

# Model UI connects to the Ollama API
export MODEL_UI_API_BASE=${MODEL_UI_API_BASE:-http://localhost:${OLLAMA_PORT}}

# Persistent model storage in workspace
export OLLAMA_MODELS="${OLLAMA_MODELS:-${WORKSPACE:-/workspace}/ollama/models}"
mkdir -p "${OLLAMA_MODELS}"
