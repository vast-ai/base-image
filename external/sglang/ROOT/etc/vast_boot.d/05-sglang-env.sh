#!/bin/bash

# Environment variables to be used by sglang serve and for terminal operation

# Do not reqire this to be set in the template unles user wants an override
if [[ -z $PORTAL_CONFIG ]]; then
    export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:8000:18000:/docs:SGLang API|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal"
fi

# SGLANG_MODEL takes precedence over serverless convention if set
export MODEL_NAME="${SGLANG_MODEL:-$MODEL_NAME}"
export SGLANG_MODEL="$MODEL_NAME"

# Auto apply parallelism
export AUTO_PARALLEL=${AUTO_PARALLEL:-${USE_ALL_GPUS:-true}}
