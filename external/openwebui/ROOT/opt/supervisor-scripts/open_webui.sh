#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
[[ "${SERVERLESS:-false}" = "false" ]] && . "${utils}/exit_portal.sh" "open webui"

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Wait for Ollama to be ready (server up + model pulled), with timeout
OLLAMA_WAIT_TIMEOUT=${OLLAMA_WAIT_TIMEOUT:-300}
elapsed=0
while [ ! -f "/tmp/.ollama_ready" ]; do
    if [ $elapsed -ge $OLLAMA_WAIT_TIMEOUT ]; then
        echo "Timeout after ${OLLAMA_WAIT_TIMEOUT}s waiting for Ollama to be ready"
        exit 1
    fi
    echo "$PROC_NAME startup paused until Ollama is ready (/tmp/.ollama_ready not present)"
    sleep 5
    elapsed=$((elapsed + 5))
done

# Environment defaults for Open WebUI
export DATA_DIR="${OPEN_WEBUI_DATA_DIR:-${WORKSPACE:-/workspace}/data}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:${OLLAMA_PORT:-21434}}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export OPENAI_API_BASE_URL="${OPENAI_API_BASE_URL:-}"

# Secret key — match upstream logic: check env, then key file, then generate
KEY_FILE="${WEBUI_SECRET_KEY_FILE:-${DATA_DIR}/.webui_secret_key}"
if [[ -z "${WEBUI_SECRET_KEY}" && -z "${WEBUI_JWT_SECRET_KEY}" ]]; then
    if [[ ! -e "$KEY_FILE" ]]; then
        echo "Generating WEBUI_SECRET_KEY"
        (umask 077 && head -c 12 /dev/urandom | base64 > "$KEY_FILE")
    fi
    echo "Loading WEBUI_SECRET_KEY from $KEY_FILE"
    WEBUI_SECRET_KEY=$(cat "$KEY_FILE")
fi
export WEBUI_SECRET_KEY

# Resolve Python interpreter once for consistent use
PYTHON_CMD=$(command -v python3 || command -v python)

# Playwright browser install if configured
if [[ "${WEB_LOADER_ENGINE,,}" == "playwright" && -z "${PLAYWRIGHT_WS_URL}" ]]; then
    echo "Installing Playwright browsers..."
    playwright install chromium
    playwright install-deps chromium
    "$PYTHON_CMD" -c "import nltk; nltk.download('punkt_tab')"
fi

# CUDA library paths (upstream pattern)
if [[ "${USE_CUDA_DOCKER,,}" == "true" ]]; then
    export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+${LD_LIBRARY_PATH}:}/usr/local/lib/python3.11/site-packages/torch/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib"
fi

HOST="127.0.0.1"
PORT="17500"
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

# Launch via uvicorn directly (matches upstream start_ollama_docker.sh)
cd /app/backend || exit 1
exec "$PYTHON_CMD" -m uvicorn open_webui.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --forwarded-allow-ips '*' \
    --workers "$UVICORN_WORKERS"
