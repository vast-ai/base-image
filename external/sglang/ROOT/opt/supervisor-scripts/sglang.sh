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

## Automatically size the parallelism args to the instance
#
# ---- unit-tested block: tools/imagegen/tests/test_sglang_args_sh.py ----
# Everything between these markers is pure string assembly and is covered by a
# table-driven test. Edit here and the table there together.
#
AUTO_PARALLEL_ARGS=""
# Rewrite var name
AUTO_PARALLEL="${AUTO_PARALLEL:-true}"

# What the user pinned themselves. sglang's own spelling is the SHORT one
# (--tp-size); --tensor-parallel-size is the alias, and the guard this replaced
# matched on the substring "parallel-size", so it saw the alias and missed the
# primary. A template pinning --tp-size 2 therefore got our --tensor-parallel-size
# $GPU_COUNT appended alongside it: two conflicting values for one setting.
_pinned_tp=""
if [[ ${SGLANG_ARGS:-} =~ (^|[[:space:]])(--tp-size|--tensor-parallel-size|--tp)[[:space:]=]+([0-9]+) ]]; then
    _pinned_tp="${BASH_REMATCH[3]}"
fi
# Data/pipeline parallelism also decide the layout, so either one likewise means
# "the user is driving" and auto-TP must stay out of it.
_pinned_other_parallel=""
if [[ ${SGLANG_ARGS:-} =~ (^|[[:space:]])(--dp-size|--data-parallel-size|--pp-size|--pipeline-parallel-size)[[:space:]=]+ ]]; then
    _pinned_other_parallel="yes"
fi

if [[ "${AUTO_PARALLEL,,}" = "true" && -z "$_pinned_tp" && -z "$_pinned_other_parallel" ]]; then
    AUTO_PARALLEL_ARGS="--tensor-parallel-size $GPU_COUNT"
fi

# The tensor-parallel size sglang will ACTUALLY run with. Not $GPU_COUNT: that is
# only the answer when we supplied the arg ourselves. With AUTO_PARALLEL=false, or
# with a dp/pp pin suppressing our TP, nothing sets it and sglang's own default of 1
# is what applies.
if [[ -n "$_pinned_tp" ]]; then
    _effective_tp="$_pinned_tp"
elif [[ -n "$AUTO_PARALLEL_ARGS" ]]; then
    _effective_tp="$GPU_COUNT"
else
    _effective_tp=1
fi

# Expert parallelism is spelled --ep-size N here; --enable-expert-parallel is
# vLLM's spelling and does nothing on this server. Because N cannot be written
# into a static template arg string, translate the portable flag into the sized one
# and drop the original.
#
# Without it Qwen3.8-Flash-Next-FP8 will not load: sglang reports
#   (moe_intermediate_size=640 / moe_tp_size=4) % weight_block_size_n=128 != 0
# because moe_tp_size is tp_size/ep_size and ep_size defaulted to 1.
#
# N is the EFFECTIVE tp, and the failure is symmetric: sizing from $GPU_COUNT while
# the user pinned a smaller tp gives tp=2 with ep=8 and the same arithmetic dies the
# other way round. An ep that does not divide the tp is always wrong, so ep tracks
# whatever tp turns out to be — and at tp=1 there is nothing to spread experts
# across, so the honest answer is to add nothing.
#
# Every branch SAYS what it did. This is the one place a flag the operator wrote
# does not reach sglang verbatim, and /var/log/sglang.log is where they will look.
EP_ARGS=""
if [[ ${SGLANG_ARGS:-} =~ (^|[[:space:]])--enable-expert-parallel ]]; then
    # Strip the flag AND an attached value: vLLM accepts --enable-expert-parallel=True,
    # and removing only the name would leave a bare "=True" for sglang to choke on.
    # Anchored on a separator as defence in depth. --no-enable-expert-parallel is
    # already safe without it (the match needs TWO dashes before `enable` and the
    # negation offers one, verified both ways), but the reasoning is easy to get
    # backwards and a one-dash spelling would shred it silently.
    SGLANG_ARGS="$(sed -E 's/(^|[[:space:]])--enable-expert-parallel(=[^[:space:]]*)?//g' <<< "${SGLANG_ARGS:-}")"
    if [[ $SGLANG_ARGS =~ (^|[[:space:]])(--ep-size|--expert-parallel-size|--ep)[[:space:]=]+ ]]; then
        echo "sglang: dropped --enable-expert-parallel (vLLM spelling); keeping your own --ep-size"
    elif [[ $_effective_tp =~ ^[0-9]+$ ]] && (( _effective_tp > 1 )); then
        EP_ARGS="--ep-size ${_effective_tp}"
        echo "sglang: translated --enable-expert-parallel (vLLM spelling) to --ep-size ${_effective_tp}"
    else
        echo "sglang: dropped --enable-expert-parallel — tensor-parallel size is ${_effective_tp:-unset}, so expert parallelism has nothing to spread across"
    fi
fi

AUTO_PARALLEL_ARGS="${AUTO_PARALLEL_ARGS} ${EP_ARGS}"
# ---- end unit-tested block ----

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
