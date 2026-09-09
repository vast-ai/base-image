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
# vast_agents/sglang.md) and is appended AFTER it on the launch line, so a flag there
# WINS. Read it here instead of inline at the launch line, so the sizing below sees
# every arg that will actually reach sglang — a --tp-size hidden in the conf file used
# to be invisible to this logic and silently defeated it. Path is a variable so the
# test table can point at a fixture.
SGLANG_ARGS_CONF="${SGLANG_ARGS_CONF:-/etc/sglang-args.conf}"
_conf_args=""
# `if`, not `[[ ]] && …`: the compound leaks a non-zero rc when the file is absent,
# which is the common case and is not an error.
if [[ -f "$SGLANG_ARGS_CONF" ]]; then
    _conf_args="$(cat "$SGLANG_ARGS_CONF")"
fi

# Detection order mirrors precedence: the conf is appended last, so argparse takes ITS
# value on a duplicate flag, and the first match below must therefore be the conf's.
_all_args="${_conf_args} ${SGLANG_ARGS:-}"

# What the operator pinned themselves. Both long spellings reach the same argparse
# dest, and each has a common short form that argparse resolves by abbreviation.
_pinned_tp=""
if [[ $_all_args =~ (^|[[:space:]])--(tp|tp-size|tensor-parallel|tensor-parallel-size)[[:space:]=]+([0-9]+) ]]; then
    _pinned_tp="${BASH_REMATCH[3]}"
fi

# The `parallel-size` substring test is DELIBERATELY left as it was, apart from also
# honouring the short tp spelling above (which it cannot see, so a template pinning
# --tp-size 2 was getting our --tensor-parallel-size $GPU_COUNT appended beside it —
# two values for one setting).
#
# Do NOT widen it to --dp-size/--pp-size. That was tried and it breaks a working
# configuration: sglang asserts tp_size % dp_size == 0 under --enable-dp-attention, so
# suppressing the automatic tp for `--dp-size 8` leaves tp at its default of 1 and the
# server dies on `1 % 8` before it serves anything. The canonical large-MoE recipe
# depends on the automatic tp still being added alongside a dp pin.
if [[ "${AUTO_PARALLEL,,}" = "true" && -z "$_pinned_tp" ]] && ! [[ $_all_args =~ parallel-size ]]; then
    AUTO_PARALLEL_ARGS="--tensor-parallel-size $GPU_COUNT"
fi

# The tensor-parallel size sglang will ACTUALLY run with. Not $GPU_COUNT: that is only
# the answer when we supplied the arg ourselves. If nothing sets it, sglang's own
# default of 1 applies.
if [[ -n "$_pinned_tp" ]]; then
    _effective_tp="$_pinned_tp"
elif [[ -n "$AUTO_PARALLEL_ARGS" ]]; then
    _effective_tp="$GPU_COUNT"
else
    _effective_tp=1
fi

# Expert parallelism is spelled --ep-size N here. --enable-expert-parallel is vLLM's
# spelling, and sglang parses its argv strictly, so passing it through is not a no-op:
# argparse exits 2 with "unrecognized arguments" and the server never starts. Because N
# cannot be written into a static template arg string, translate the flag into the
# sized one and drop the original.
#
# Without expert parallelism Qwen3.8-Flash-Next-FP8 will not load: sglang reports
#   (moe_intermediate_size=640 / moe_tp_size=4) % weight_block_size_n=128 != 0
# because moe_tp_size is tp_size/ep_size and ep_size defaulted to 1.
#
# N is the EFFECTIVE tp, and the failure is symmetric: sizing from $GPU_COUNT while the
# operator pinned a smaller tp gives tp=2 with ep=8 and the same arithmetic dies the
# other way round. An ep that does not divide the tp is always wrong, so ep tracks
# whatever tp turns out to be — and at tp=1 there is nothing to spread experts across,
# so the honest answer is to add nothing.
#
# Every branch SAYS what it did. This is the one place a flag the operator wrote does
# not reach sglang verbatim, and /var/log/sglang.log is where they will look.
EP_ARGS=""
if [[ $_all_args =~ (^|[[:space:]])--enable-expert-parallel ]]; then
    # Strip the flag AND an attached value from BOTH sources. vLLM renders this as a
    # BooleanOptionalAction, which takes no value, so `=X` is not a shape vLLM itself
    # accepts — the value group is here so that a hand-written one cannot survive the
    # strip as a bare `=X` positional for sglang to choke on.
    _strip='s/(^|[[:space:]])--enable-expert-parallel(=[^[:space:]]*)?//g'
    _wants_ep=yes
    # An attached FALSE asks for the opposite. Enabling on it would invert the operator.
    [[ $_all_args =~ --enable-expert-parallel=([Ff]alse|FALSE|0|[Nn]o|NO) ]] && _wants_ep=""
    SGLANG_ARGS="$(sed -E "$_strip" <<< "${SGLANG_ARGS:-}")"
    _conf_args="$(sed -E "$_strip" <<< "${_conf_args}")"
    _all_args="${_conf_args} ${SGLANG_ARGS:-}"

    if [[ -z "$_wants_ep" ]]; then
        echo "sglang: dropped --enable-expert-parallel (a false value was attached, so expert parallelism was NOT requested)"
    elif [[ $_all_args =~ (^|[[:space:]])--(ep|ep-size|expert-parallel|expert-parallel-size)[[:space:]=]+ ]]; then
        echo "sglang: dropped --enable-expert-parallel (vLLM spelling); keeping your own expert-parallel size"
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

# Complex args from $SGLANG_ARGS_CONF were read (and rewritten) above; they are
# appended last so a flag there still wins, exactly as before.
eval "pty sglang serve --model-path "${SGLANG_MODEL:-}" ${SGLANG_ARGS:-} ${AUTO_PARALLEL_ARGS} ${_conf_args}" 2>&1
