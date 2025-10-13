#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "vllm"

# Activate the venv
. /venv/main/bin/activate

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

# Launch vllm
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
AUTO_PARALLEL=${AUTO_PARALLEL:-${USE_ALL_GPUS:-false}}
if [[ "${AUTO_PARALLEL,,}" = "true" ]] && ! [[ $VLLM_ARGS =~ tensor-parallel-size || $VLLM_ARGS =~ data-parallel-size ]]; then
    if [[ $VLLM_ARGS =~ enable-expert-parallel ]]; then
        AUTO_PARALLEL_ARGS="--tensor-parallel-size 1 --data-parallel-size $GPU_COUNT"
    else
        AUTO_PARALLEL_ARGS="--tensor-parallel-size $GPU_COUNT"
    fi
fi

vllm serve "${VLLM_MODEL:-}" ${VLLM_ARGS:---host 127.0.0.1 --port 18000} ${AUTO_PARALLEL_ARGS} 2>&1
