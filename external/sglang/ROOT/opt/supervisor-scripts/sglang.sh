#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
[[ "${SERVERLESS:-false}" = "false" ]] && . "${utils}/exit_portal.sh" "sglang"

# Check we are actually trying to serve a model
if [[ -z "${SGLANG_MODEL:-}" ]]; then
    echo "Refusing to start ${PROC_NAME} (SGLANG_MODEL not set)"
    sleep 6
    exit 0
fi

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Launch SGLang
cd ${WORKSPACE}

## Automatically use all GPUs
AUTO_PARALLEL_ARGS=""
# Rewrite var name
AUTO_PARALLEL="${AUTO_PARALLEL:-true}"
if [[ "${AUTO_PARALLEL,,}" = "true" ]] && ! [[ $SGLANG_ARGS =~ parallel-size ]]; then
    AUTO_PARALLEL_ARGS="--tensor-parallel-size $GPU_COUNT"
fi

# Force Caches to be written in workspace (vols)
export HOME=${WORKSPACE}
# Read complex args from /etc/sglang-args.conf if env vars were unsuitable
eval "sglang serve --model-path "${SGLANG_MODEL:-}" ${SGLANG_ARGS:-} ${AUTO_PARALLEL_ARGS} $([[ -f /etc/sglang-args.conf ]] && cat /etc/sglang-args.conf)" 2>&1
