#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
[[ "${SERVERLESS:-false}" = "false" ]] && . "${utils}/exit_portal.sh" "ollama"

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Launch Ollama in the background so we can pull the model before exposing the API
ollama serve ${OLLAMA_ARGS:-} 2>&1 &
OLLAMA_PID=$!

# Wait for the server to become ready
max_attempts=60
attempt=1

while true; do
    if curl -sf http://localhost:${OLLAMA_PORT:-21434}/api/tags > /dev/null 2>&1; then
        echo "Ollama server is ready"
        break
    fi

    if ! kill -0 $OLLAMA_PID 2>/dev/null; then
        echo "Ollama server process exited unexpectedly"
        exit 1
    fi

    if [ $attempt -ge $max_attempts ]; then
        echo "Timeout waiting for Ollama server to start"
        exit 1
    fi

    echo "Waiting for Ollama server to be ready (attempt $attempt/$max_attempts)..."
    sleep 5
    ((attempt++))
done

# Pull the model if set
if [[ -n "${OLLAMA_MODEL:-}" ]]; then
    echo "Pulling model: ${OLLAMA_MODEL}"
    if ! ollama pull "${OLLAMA_MODEL}"; then
        echo "Failed to pull model: ${OLLAMA_MODEL}"
        # Ensure the Ollama server process does not keep running after a failed pull
        if kill -0 $OLLAMA_PID 2>/dev/null; then
            kill "$OLLAMA_PID" 2>/dev/null || true
            wait "$OLLAMA_PID" 2>/dev/null || true
        fi
        exit 1
    fi
    echo "Model pull complete: ${OLLAMA_MODEL}"
fi

wait $OLLAMA_PID
