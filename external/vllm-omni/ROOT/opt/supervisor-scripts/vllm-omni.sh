#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
[[ -f /venv/main/bin/activate ]] && . /venv/main/bin/activate
[[ "${SERVERLESS:-false}" = "false" ]] && . "${utils}/exit_portal.sh" "vllm"

# Check we are actually trying to serve a model
if [[ -z "${VLLM_MODEL:-}" ]]; then
    echo "Refusing to start ${PROC_NAME} (VLLM_MODEL not set)"
    sleep 6
    exit 0
fi

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Launch vllm-omni
cd "${WORKSPACE}"

# User has not specified a remote Ray server
if [[ -z "$RAY_ADDRESS" || "$RAY_ADDRESS" = "127.0.0.1"* ]]; then
    export RAY_ADDRESS="127.0.0.1:6379"

    # Wait until ps aux shows ray is running
    max_attempts=30
    attempt=1

    while true; do
        if ps aux | grep -v grep | grep -q "gcs_server"; then
            echo "Ray process detected - continuing"
            break
        fi

        if [ $attempt -ge $max_attempts ]; then
            echo "Timeout waiting for Ray process to start"
            exit 1
        fi

        echo "Waiting for Ray process to start (attempt $attempt/$max_attempts)..."
        sleep 2
        ((attempt++))
    done
fi

## Automatically use all GPUs
AUTO_PARALLEL_ARGS=""
# Rewrite var name
AUTO_PARALLEL="${AUTO_PARALLEL:-true}"
if [[ "${AUTO_PARALLEL,,}" = "true" ]] && ! [[ $VLLM_ARGS =~ tensor-parallel-size || $VLLM_ARGS =~ data-parallel-size ]]; then
    if [[ $VLLM_ARGS =~ enable-expert-parallel ]]; then
        AUTO_PARALLEL_ARGS="--tensor-parallel-size 1 --data-parallel-size $GPU_COUNT"
    else
        AUTO_PARALLEL_ARGS="--tensor-parallel-size $GPU_COUNT"
    fi
fi

# ---------------------------------------------------------------------------
# Engine crash watchdog
# ---------------------------------------------------------------------------
# vLLM Omni's engine core can crash on bad requests while the HTTP server
# stays alive. Supervisor sees a running process and never restarts.
# A background watchdog tails the log file (written by logging.sh) and
# kills the script on fatal engine errors to trigger supervisor's autorestart.
#
# Set VLLM_CRASH_PATTERN to override the default detection regex.
# Set VLLM_WATCHDOG=false to disable crash monitoring entirely.

CRASH_PATTERN=${VLLM_CRASH_PATTERN:-"EngineDeadError|AsyncEngineDeadError|engine process.*(dead|died|failed)|ENGINE_DEAD|Background loop has errored"}

if [[ "${VLLM_WATCHDOG:-true}" != "false" ]]; then
    (
        while [[ ! -f "$logfile" ]]; do sleep 1; done
        tail -n 0 -f "$logfile" | while IFS= read -r line; do
            if [[ "$line" =~ $CRASH_PATTERN ]]; then
                echo "[watchdog] Fatal engine error detected — terminating vLLM for restart"
                echo "[watchdog] Matched: ${BASH_REMATCH[0]}"
                kill $$ 2>/dev/null
                break
            fi
        done
    ) &
fi

cd "${WORKSPACE}/vllm-omni"
# Read complex args from /etc/vllm-args.conf if env vars were unsuitable
eval "pty vllm serve "${VLLM_MODEL:-}" ${VLLM_ARGS:-} ${AUTO_PARALLEL_ARGS} $([[ -f /etc/vllm-args.conf ]] && cat /etc/vllm-args.conf)" 2>&1
