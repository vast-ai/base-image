#!/bin/bash

# Environment variables to be used by vllm-omni serve and for terminal operation

# Do not require this to be set in the template unless user wants an override
if [[ -z $PORTAL_CONFIG ]]; then
    export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:7860:17860:/:Model UI|localhost:8000:18000:/docs:vLLM-Omni API|localhost:8265:28265:/:Ray Dashboard|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal"
fi

# Strip Model UI from portal config when disabled
if [[ "${ENABLE_UI,,}" = "false" ]]; then
    export PORTAL_CONFIG="${PORTAL_CONFIG//|localhost:7860:17860:\/:Model UI/}"
fi

# VLLM_MODEL takes precedence over serverless convention if set
export MODEL_NAME="${VLLM_MODEL:-$MODEL_NAME}"
export VLLM_MODEL="$MODEL_NAME"

# Configure vLLM cache
export VLLM_CACHE_ROOT=${VLLM_CACHE_ROOT:-${WORKSPACE:-/workspace}/.vllm_cache}
mkdir -p "${VLLM_CACHE_ROOT}"

# Ray config defaults
export RAY_ARGS=${RAY_ARGS:---head --port 6379 --dashboard-host 127.0.0.1 --dashboard-port 28265}
export RAY_ADDRESS=${RAY_ADDRESS:-127.0.0.1:6379}

# Auto apply parallelism
export AUTO_PARALLEL=${AUTO_PARALLEL:-${USE_ALL_GPUS:-true}}
