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

# /etc/sglang-args.conf is documented as interchangeable with SGLANG_ARGS (README,
# vast_agents/sglang.md) and is appended AFTER our args on the launch line, so a flag
# there beats ours. Read it here rather than inline at the launch line, so the decision
# below sees every arg that will actually reach sglang.
SGLANG_ARGS_CONF="${SGLANG_ARGS_CONF:-/etc/sglang-args.conf}"
_conf_args=""
# `if`, not `[[ ]] && …`: the compound leaks a non-zero rc when the file is absent,
# which is the common case and is not an error.
if [[ -f "$SGLANG_ARGS_CONF" ]]; then
    _conf_args="$(cat "$SGLANG_ARGS_CONF")"
fi
_all_args="${_conf_args} ${SGLANG_ARGS:-}"

# Has the operator pinned a parallel size themselves? A BOOLEAN on purpose — we never
# read the value; see the expert-parallel note below for why.
#
# The `parallel-size` substring is the original test, kept exactly as it was so that
# --data-parallel-size / --pipeline-parallel-size keep behaving as they always have.
# Do NOT extend it to --dp-size/--pp-size: that was tried and it breaks a working
# configuration, because sglang asserts tp_size % dp_size == 0 under
# --enable-dp-attention, so suppressing the automatic tp for `--dp-size 8` leaves tp at
# its default of 1 and the server dies on `1 % 8` before serving anything.
#
# What it genuinely cannot see is --tp-size, sglang's short spelling for the SAME
# setting the long one names. A template pinning that was getting our
# --tensor-parallel-size $GPU_COUNT appended beside it: two values for one setting.
_operator_pinned_parallelism=""
if [[ $_all_args =~ parallel-size ]] ||
   [[ $_all_args =~ (^|[[:space:]])--(tp|tp-size|tensor-parallel)[[:space:]=] ]]; then
    _operator_pinned_parallelism=yes
fi

if [[ "${AUTO_PARALLEL,,}" = "true" && -z "$_operator_pinned_parallelism" ]]; then
    AUTO_PARALLEL_ARGS="--tensor-parallel-size $GPU_COUNT"
fi

# Expert parallelism is spelled --ep-size N here. --enable-expert-parallel is vLLM's
# spelling, and sglang parses its argv strictly, so passing it through is not a
# harmless no-op: argparse exits 2 with "unrecognized arguments" and the server never
# starts. Without expert parallelism actually enabled, Qwen3.8-Flash-Next-FP8 will not
# load: sglang reports
#   (moe_intermediate_size=640 / moe_tp_size=4) % weight_block_size_n=128 != 0
# because moe_tp_size is tp_size/ep_size and ep_size defaulted to 1.
#
# We translate ONLY when we chose the tensor-parallel size ourselves, and we size N to
# match it. That is the whole point of the portable flag: N must equal the instance GPU
# count, which is why it cannot be written into a static template arg string. A
# template that pins its own tp already knows its shape and can write --ep-size
# directly.
#
# Declining in every other case is the deliberate part. sglang derives
# moe_tp_size = tp_size/ep_size, so an ep that does not divide the tp fails the load
# with an arithmetic error naming neither flag — and an ep we invented against a tp we
# did not choose is exactly how that happens (tp=2 pinned, ep=$GPU_COUNT=8, moe_tp_size
# 0.25). Guessing here can only produce that failure; saying so cannot.
#
# Every branch SAYS what it did. This is the one place a flag the operator wrote does
# not reach sglang verbatim, and /var/log/sglang.log is where they will look.
EP_ARGS=""
if [[ $_all_args =~ (^|[[:space:]])--enable-expert-parallel ]]; then
    # Strip the flag AND an attached value from BOTH sources. vLLM renders this as a
    # BooleanOptionalAction, which takes no value, so `=X` is not a shape vLLM itself
    # accepts — the value group is here so a hand-written one cannot survive the strip
    # as a bare `=X` positional for sglang to choke on.
    _strip='s/(^|[[:space:]])--enable-expert-parallel(=[^[:space:]]*)?//g'
    _wants_ep=yes
    # An attached FALSE asks for the opposite. Enabling on it would invert the operator.
    [[ $_all_args =~ --enable-expert-parallel=([Ff]alse|FALSE|0|[Nn]o|NO) ]] && _wants_ep=""
    SGLANG_ARGS="$(sed -E "$_strip" <<< "${SGLANG_ARGS:-}")"
    _conf_args="$(sed -E "$_strip" <<< "${_conf_args}")"
    _all_args="${_conf_args} ${SGLANG_ARGS:-}"

    if [[ -z "$_wants_ep" ]]; then
        echo "sglang: dropped --enable-expert-parallel — a false value was attached, so expert parallelism was NOT requested"
    elif [[ $_all_args =~ (^|[[:space:]])--(ep|ep-size|expert-parallel|expert-parallel-size)[[:space:]=] ]]; then
        echo "sglang: dropped --enable-expert-parallel (vLLM spelling); keeping the expert-parallel size you set yourself"
    elif [[ -n "$_operator_pinned_parallelism" ]]; then
        echo "sglang: dropped --enable-expert-parallel (vLLM spelling) — you pinned the parallel size yourself, so add an explicit --ep-size N. It must divide your tensor-parallel size or the model will not load."
    elif [[ -z "$AUTO_PARALLEL_ARGS" ]]; then
        echo "sglang: dropped --enable-expert-parallel — AUTO_PARALLEL is off, so nothing sets a tensor-parallel size and there is nothing to spread experts across"
    elif [[ $GPU_COUNT =~ ^[0-9]+$ ]] && (( GPU_COUNT > 1 )); then
        EP_ARGS="--ep-size $GPU_COUNT"
        echo "sglang: translated --enable-expert-parallel (vLLM spelling) to --ep-size $GPU_COUNT"
    else
        echo "sglang: dropped --enable-expert-parallel — tensor-parallel size is ${GPU_COUNT:-unset}, so expert parallelism has nothing to spread across"
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

# Complex args from $SGLANG_ARGS_CONF were read (and rewritten) above; they are
# appended last so a flag there still wins, exactly as before.
eval "pty sglang serve --model-path "${SGLANG_MODEL:-}" ${SGLANG_ARGS:-} ${AUTO_PARALLEL_ARGS} ${_conf_args}" 2>&1
