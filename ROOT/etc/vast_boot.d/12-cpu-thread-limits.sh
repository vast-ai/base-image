#!/bin/bash
# 12-cpu-thread-limits.sh — container-aware CPU thread caps (ADR 0014).
#
# A Vast instance is a cgroup-limited container. On some hosts the container sees
# far more CPUs than it is entitled to (unrestricted cpuset -> nproc reports the
# whole physical host, e.g. 384) while its CPU *quota* is a small slice (e.g. ~46
# cores) AND its pids budget (pids.max) is pathologically low (e.g. 1024 instead
# of the usual ~256/allocated-core). Native/ML runtimes (OpenMP, OpenBLAS, MKL,
# torch, Rust rayon/hf_xet) size their thread pools to the *visible* core count,
# so a few pools exhaust pids.max and pthread_create fails with EAGAIN
# ("Resource temporarily unavailable") — crashing model downloads and inference.
#
# This hook is a SAFETY VALVE, not a fleet-wide tuner: it only intervenes when the
# pids budget is dangerously low relative to the visible cores (see the trigger
# below). On the overwhelming majority of hosts (healthy pids budget) it is a
# no-op and leaves every runtime at its own defaults. When it does fire, it caps
# the thread pools to the CPU entitlement so pools stay within the pid budget.
#
# It is SOURCED by boot_default.sh after 10-prep-env.sh (which creates
# /etc/environment AND, with export_env=true, sources /etc/environment + the
# user's ${WORKSPACE}/.env into the boot shell). It writes a managed block into
# /etc/environment — recomputed every boot — which every supervisor service
# re-reads (utils/environment.sh), so a capped service that restarts still sees
# the caps. User/template values are never overwritten. When a formerly-capped
# instance later boots on a healthy host (e.g. the host's pids.max is fixed), the
# stale block is removed AND the vars it set are cleared from the boot shell (so
# supervisord, launched later in this same shell, does not inherit them), while
# any real user value in /etc/environment or ${WORKSPACE}/.env is preserved.

# ── Pure decision (unit-tested with synthetic inputs) ────────────────
# Given pids_max, visible cores, and quota cores, echo the cap to apply, or echo
# nothing to mean "no-op / leave runtime defaults". Kept side-effect free so
# tests/base/56-cpu-thread-limits.sh can exercise every branch off-host.
_vast_thread_cap() {
    local pids_max="$1" visible="$2" quota="$3"
    local ceiling="${VAST_CPU_THREAD_CEILING:-16}"
    # A finite, numeric pids budget and a visible core count are required to
    # reason at all. "max"/unlimited/unreadable pids -> nothing to protect.
    [[ "$pids_max" =~ ^[0-9]+$ ]] || return 0
    [[ "$visible"  =~ ^[0-9]+$ ]] && (( visible > 0 )) || return 0
    [[ "$ceiling"  =~ ^[0-9]+$ ]] && (( ceiling >= 2 )) || ceiling=16
    # Trigger: only intervene when the budget is dangerously low. Healthy Vast
    # hosts run ~30-120 pids per visible core (pids.max ~= 256/allocated-core,
    # allocated << visible); the pathological host that motivated this ran 2.7.
    # 16 sits well inside that gap.
    (( pids_max >= visible * 16 )) && return 0
    # Cap to the CPU entitlement, clamped: never above the ceiling (bounds a
    # single pool's draw on the pid budget regardless of a generous quota), never
    # below 2 (protect inference latency on a sub-2-core quota). No readable quota
    # -> fall back to the ceiling (still far below the visible-core default).
    local cap
    if [[ "$quota" =~ ^[0-9]+$ ]] && (( quota > 0 )); then cap="$quota"; else cap="$ceiling"; fi
    (( cap > ceiling )) && cap="$ceiling"
    (( cap < 2 )) && cap=2
    echo "$cap"
}

# ── cgroup readers (v2 first, then v1; empty/echo-nothing on absence) ──
_vast_read_pids_max() {
    if   [[ -r /sys/fs/cgroup/pids.max ]];       then cat /sys/fs/cgroup/pids.max        # v2
    elif [[ -r /sys/fs/cgroup/pids/pids.max ]];  then cat /sys/fs/cgroup/pids/pids.max   # v1
    fi
}
# Echo the container's CPU quota in whole cores (rounded), or nothing if unlimited.
_vast_read_quota_cores() {
    local q p
    if [[ -r /sys/fs/cgroup/cpu.max ]]; then                       # v2: "<quota|max> <period>"
        read -r q p < /sys/fs/cgroup/cpu.max
        [[ "$q" == "max" ]] && return 0
    elif [[ -r /sys/fs/cgroup/cpu/cpu.cfs_quota_us ]]; then        # v1
        q=$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us 2>/dev/null)
        p=$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us 2>/dev/null)
        (( q <= 0 )) 2>/dev/null && return 0                       # -1 == unlimited
    else
        return 0
    fi
    [[ "$q" =~ ^[0-9]+$ ]] && [[ "$p" =~ ^[0-9]+$ ]] && (( p > 0 )) || return 0
    echo $(( (q + p / 2) / p ))                                    # rounded to whole cores
}

# ── Managed-block plumbing ────────────────────────────────────────────
_VAST_TCAP_VARS=(OMP_NUM_THREADS OPENBLAS_NUM_THREADS MKL_NUM_THREADS
                 NUMEXPR_NUM_THREADS NUMEXPR_MAX_THREADS VECLIB_MAXIMUM_THREADS
                 RAYON_NUM_THREADS HF_HUB_DISABLE_XET)
_VAST_TCAPS_BEGIN="# VAST_CPU_THREAD_CAPS_BEGIN (ADR 0014, managed — do not edit)"
_VAST_TCAPS_END="# VAST_CPU_THREAD_CAPS_END"

# True if VAR is set by the user/template/launch env — i.e. present OUTSIDE this
# hook's managed block, in /etc/environment (10-prep-env.sh dumps the launch env
# there, so `-e VAR=...` at first launch lands there) OR in ${WORKSPACE}/.env (the
# documented per-instance override). Consulting both is what stops the no-op path
# from clobbering a legitimate user value that never appears in /etc/environment.
_vast_user_set() {
    local var="$1" file="${2:-/etc/environment}" envf="${WORKSPACE:-/workspace}/.env"
    if [[ -f "$file" ]] && sed "/VAST_CPU_THREAD_CAPS_BEGIN/,/VAST_CPU_THREAD_CAPS_END/d" "$file" \
            | grep -qE "^[[:space:]]*(export[[:space:]]+)?${var}="; then return 0; fi
    [[ -f "$envf" ]] && grep -qE "^[[:space:]]*(export[[:space:]]+)?${var}=" "$envf"
}

# Echo the variable names actually assigned inside the managed block of <file>.
# (The block records exactly what we set; unsetting by this parsed list — not the
# static _VAST_TCAP_VARS — keeps strip and unset symmetric across hook versions
# and can never touch a var the user set outside the block.)
_vast_block_vars() {
    local file="${1:-/etc/environment}"
    [[ -f "$file" ]] || return 0
    sed -n '/VAST_CPU_THREAD_CAPS_BEGIN/,/VAST_CPU_THREAD_CAPS_END/p' "$file" 2>/dev/null \
        | grep -oE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' | tr -d '[:blank:]='
}

# Remove the managed block from <file> (idempotent). Guarded by a presence check
# so a clean file is never rewritten.
_vast_strip_caps() {
    local file="${1:-/etc/environment}"
    [[ -f "$file" ]] && grep -qF "VAST_CPU_THREAD_CAPS_BEGIN" "$file" \
        && sed -i "/VAST_CPU_THREAD_CAPS_BEGIN/,/VAST_CPU_THREAD_CAPS_END/d" "$file"
    return 0
}

# Write a fresh managed block to <file>, stripping any prior one first (migration-
# safe). Exports each capped var into the current shell so supervisord (launched
# later this boot) inherits it; leaves user/template vars untouched.
_vast_write_caps() {
    local cap="$1" file="${2:-/etc/environment}" var val
    [[ -n "$cap" ]] || return 0
    _vast_strip_caps "$file"
    {
        echo "$_VAST_TCAPS_BEGIN"
        for var in "${_VAST_TCAP_VARS[@]}"; do
            val="$cap"
            # hf_xet's Rust pool ignores the thread-count vars, so disable xet on
            # these hosts (its only cost — slower dedup transfer — applies solely
            # where it would otherwise crash). Not relied on being governed by
            # RAYON_NUM_THREADS.
            [[ "$var" == HF_HUB_DISABLE_XET ]] && val=1
            if _vast_user_set "$var" "$file"; then
                echo "# ${var}: left at user/template value"
            else
                echo "${var}=${val}"
                export "${var}=${val}"
            fi
        done
        echo "$_VAST_TCAPS_END"
    } >> "$file"
}

# The whole side-effecting decision, given the computed cap ("" == no-op host):
#  - cap set  -> write/refresh the managed block.
#  - cap empty but a stale block exists (formerly-capped instance now on a healthy
#    host) -> remove the block, unset ONLY the vars that block set from the boot
#    shell, then re-source the authoritative files so any real user value
#    (including one added to .env since the cap was written) is restored.
_vast_apply_caps() {
    local cap="$1" file="${2:-/etc/environment}" var
    if [[ -n "$cap" ]]; then
        _vast_write_caps "$cap" "$file"
        return 0
    fi
    [[ -f "$file" ]] && grep -qF "VAST_CPU_THREAD_CAPS_BEGIN" "$file" || return 0
    local stale=(); mapfile -t stale < <(_vast_block_vars "$file")
    _vast_strip_caps "$file"
    for var in "${stale[@]}"; do [[ -n "$var" ]] && unset "$var"; done
    set -a
    . "$file" 2>/dev/null
    [[ -f "${WORKSPACE:-/workspace}/.env" ]] && . "${WORKSPACE:-/workspace}/.env" 2>/dev/null
    set +a
    return 0
}

# Read the live cgroup state, decide, and apply to <file>. The whole compute->apply
# wiring in one testable place: the test stubs the readers/nproc and drives this
# against a temp file (so the no-op path's strip is covered, not just applied when
# a cap exists). Always calls _vast_apply_caps — that is what makes a formerly
# capped instance shed its block on a later healthy boot.
_vast_run() {
    local file="${1:-/etc/environment}" pm vis q cap
    pm=$(_vast_read_pids_max)
    vis=$(nproc 2>/dev/null)
    q=$(_vast_read_quota_cores)
    cap=$(_vast_thread_cap "${pm:-}" "${vis:-}" "${q:-}")
    [[ -n "$cap" ]] && echo "[cpu-thread-limits] low pids budget (pids.max=${pm}, nproc=${vis}," \
        "quota=${q:-none}) — capping thread pools to ${cap}"
    _vast_apply_caps "$cap" "$file"
}

# Skip the boot action when sourced lib-only (the test loads functions this way).
[[ -n "${_VAST_THREAD_HOOK_LIB_ONLY:-}" ]] && return 0

# ── Act ───────────────────────────────────────────────────────────────
# MUST remain a brace group, NOT a subshell — the unset/export/re-source inside
# _vast_apply_caps have to reach this boot shell (which later launches supervisord
# via 65-supervisor-launch.sh).
{ _vast_run /etc/environment; } 2>/dev/null \
    || echo "[cpu-thread-limits] skipped (unexpected error reading cgroup state)"
