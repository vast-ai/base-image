#!/bin/bash

# Environment variables to be used by ollama serve and for terminal operation

# Do not require this to be set in the template unless user wants an override
if [[ -z $PORTAL_CONFIG ]]; then
    export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:7860:17860:/:Model UI|localhost:21434:11434:/:Ollama API|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal"
fi

# OLLAMA_MODEL takes precedence over serverless convention if set
export MODEL_NAME="${OLLAMA_MODEL:-$MODEL_NAME}"
export OLLAMA_MODEL="$MODEL_NAME"

# Bind Ollama to loopback ONLY (on its native port 11434). External access is via the
# authenticated Caddy edge, which proxies external 21434 -> internal 11434 over localhost.
# The upstream image bakes `ENV OLLAMA_HOST=0.0.0.0:11434`, so a plain `:-` default would
# never fire (the var is already set). Rewrite an all-interfaces host to loopback, keeping
# the port — a 0.0.0.0 bind, combined with the upstream `EXPOSE 11434`, would leave the API
# reachable unauthenticated, bypassing Caddy token auth. An operator can still set a
# non-0.0.0.0 OLLAMA_HOST to override.
case "${OLLAMA_HOST:-}" in
    ""|0.0.0.0)  OLLAMA_HOST="127.0.0.1:11434" ;;
    0.0.0.0:*)   OLLAMA_HOST="127.0.0.1:${OLLAMA_HOST##*:}" ;;
esac
export OLLAMA_HOST
export OLLAMA_PORT=${OLLAMA_HOST##*:}

# Model UI connects to the Ollama API
export MODEL_UI_API_BASE=${MODEL_UI_API_BASE:-http://localhost:${OLLAMA_PORT}}

# Persistent model storage in workspace
export OLLAMA_MODELS="${OLLAMA_MODELS:-${WORKSPACE:-/workspace}/ollama/models}"
mkdir -p "${OLLAMA_MODELS}"
