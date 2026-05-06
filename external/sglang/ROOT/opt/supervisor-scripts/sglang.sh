#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
[[ -f /venv/main/bin/activate ]] && . /venv/main/bin/activate
. "${utils}/exit_portal.sh" "sglang"

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

# unbuffer (used by `pty`) setsids its leaf into a new session, so killasgroup
# can't reach the running sglang. The kernel's PTY hangup that normally kills
# such a leaf when its master dies also doesn't kill sglang, because sglang
# serve installs a SIGHUP handler that swallows the signal. Override the
# generic cleanup trap to kill the orphaned sglang directly by command pattern
# (mirrors the approach used in aio-studio's ai-toolkit.sh for run.py).
cleanup_sglang() {
    echo "Stopping sglang..."
    pkill -TERM -f 'sglang serve' 2>/dev/null
    for _ in {1..50}; do
        pgrep -f 'sglang serve' >/dev/null 2>&1 || break
        sleep 0.1
    done
    pkill -KILL -f 'sglang serve' 2>/dev/null
}
trap cleanup_sglang EXIT INT TERM

# Read complex args from /etc/sglang-args.conf if env vars were unsuitable
eval "pty sglang serve --model-path "${SGLANG_MODEL:-}" ${SGLANG_ARGS:-} ${AUTO_PARALLEL_ARGS} $([[ -f /etc/sglang-args.conf ]] && cat /etc/sglang-args.conf)" 2>&1
