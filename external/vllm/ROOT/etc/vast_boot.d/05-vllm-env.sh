#!/bin/bash

# Environment variables to be used by vllm serve and for terminal operation

export VLLM_CACHE_ROOT=${VLLM_CACHE_ROOT:-${WORKSPACE:-/workspace}/.vllm_cache}
mkdir -p ${VLLM_CACHE_ROOT}
