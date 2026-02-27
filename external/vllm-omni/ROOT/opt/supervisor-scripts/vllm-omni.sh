#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
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
cd ${WORKSPACE}

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
# We pipe output through a monitor that watches for fatal engine errors
# and kills the process to trigger supervisor's autorestart.
#
# Set VLLM_CRASH_PATTERN to override the default detection regex.
# Set VLLM_WATCHDOG=false to disable crash monitoring entirely.

CRASH_PATTERN=${VLLM_CRASH_PATTERN:-"EngineDeadError|AsyncEngineDeadError|engine process.*(dead|died|failed)|ENGINE_DEAD|Background loop has errored"}
WATCHDOG_ENABLED=${VLLM_WATCHDOG:-true}

LOGPIPE=$(mktemp -u /tmp/vllm-logpipe.XXXXXX)
mkfifo "$LOGPIPE"

# Read complex args from /etc/vllm-args.conf if env vars were unsuitable
eval "vllm serve ${VLLM_MODEL:-} ${VLLM_ARGS:-} ${AUTO_PARALLEL_ARGS} $([[ -f /etc/vllm-args.conf ]] && cat /etc/vllm-args.conf)" > "$LOGPIPE" 2>&1 &
VLLM_PID=$!

# Pass output through to stdout; watch for crash patterns
while IFS= read -r line; do
    printf '%s\n' "$line"
    if [[ "${WATCHDOG_ENABLED,,}" = "true" ]] && [[ "$line" =~ $CRASH_PATTERN ]]; then
        echo "[watchdog] Fatal engine error detected — terminating vLLM for restart"
        echo "[watchdog] Matched: ${BASH_REMATCH[0]}"
        kill $VLLM_PID 2>/dev/null
        break
    fi
done < "$LOGPIPE"

rm -f "$LOGPIPE"
wait $VLLM_PID 2>/dev/null
exit $?
