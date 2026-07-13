#!/bin/bash
# Test: container-aware CPU thread caps (ADR 0014).
#
# Two layers:
#  1. Unit — exercise the pure decision _vast_thread_cap() with synthetic
#     (pids_max, visible, quota) tuples, including the pathological host we can no
#     longer rent on demand. This is the mutation-sensitive check: break the
#     trigger or the clamp and these assertions fail.
#  2. Live — recompute the decision from THIS host's real cgroup values and assert
#     /etc/environment agrees (a managed block iff the host is pathological, with
#     the vars at the computed cap; no block on a healthy host). Correct on either
#     kind of host.
source "$(dirname "$0")/../lib.sh"

# Load the hook's functions without running its boot action.
HOOK=/etc/vast_boot.d/12-cpu-thread-limits.sh
[[ -r "$HOOK" ]] || HOOK="$(dirname "$0")/../../../../etc/vast_boot.d/12-cpu-thread-limits.sh"
assert_file_exists "$HOOK"
_VAST_THREAD_HOOK_LIB_ONLY=1 source "$HOOK" || test_fatal "could not source $HOOK in lib-only mode"
declare -F _vast_thread_cap >/dev/null || test_fatal "_vast_thread_cap not defined after sourcing hook"

# expect <label> <got> <want>
expect() { [[ "$2" == "$3" ]] || fail_later "$1" "got '$2', want '$3'"; }

# ── 1. Unit: the pure decision ───────────────────────────────────────
# Pathological host that motivated the ADR: 384 visible, ~46 quota, pids.max=1024.
expect "pathological caps to ceiling"   "$(_vast_thread_cap 1024 384 46)"  "16"
# Healthy hosts (pids.max ~= 256/allocated-core) — all no-ops.
expect "healthy 384/46/11776 no-op"     "$(_vast_thread_cap 11776 384 46)" ""
expect "healthy 128/61/15616 no-op"     "$(_vast_thread_cap 15616 128 61)" ""
expect "healthy 128/41/10240 no-op"     "$(_vast_thread_cap 10240 128 41)" ""
# Unlimited / unreadable pids budget -> nothing to protect.
expect "pids=max no-op"                 "$(_vast_thread_cap max 384 46)"   ""
expect "pids empty no-op"               "$(_vast_thread_cap '' 384 46)"    ""
# Trigger fires but quota unreadable -> fall back to ceiling.
expect "low pids, no quota -> ceiling"  "$(_vast_thread_cap 1024 384 '')"  "16"
# Quota below the floor is clamped up to 2 (latency floor).
expect "sub-2 quota clamps to floor"    "$(_vast_thread_cap 500 384 1)"    "2"
# Quota under the ceiling is honoured as-is.
expect "small quota honoured"           "$(_vast_thread_cap 800 384 6)"    "6"
# Boundary: pids.max exactly at the trigger threshold is treated as ample.
expect "at-threshold is ample"          "$(_vast_thread_cap $((384*16)) 384 46)" ""
# Ceiling is overridable.
VAST_CPU_THREAD_CEILING=8 expect "ceiling override honoured" \
    "$(VAST_CPU_THREAD_CEILING=8 _vast_thread_cap 1024 384 46)" "8"

# ── 2. Write path: managed block, idempotency, override-preservation ──
# Exercise _vast_write_caps against a temp file (no touch to real /etc/environment).
_tmpenv=$(mktemp)
echo "OMP_NUM_THREADS=99" > "$_tmpenv"   # pretend the user set OMP at launch
_vast_write_caps 16 "$_tmpenv"
# User's OMP preserved (commented in block, its real assignment untouched).
[[ "$(grep -c '^OMP_NUM_THREADS=' "$_tmpenv")" == "1" ]] || fail_later "override-preserve" "user OMP_NUM_THREADS assignment altered/duplicated"
grep -q '^OMP_NUM_THREADS=99$' "$_tmpenv" || fail_later "override-value" "user OMP_NUM_THREADS=99 not preserved"
grep -q '^MKL_NUM_THREADS=16$'  "$_tmpenv" || fail_later "managed-set" "MKL_NUM_THREADS not set to cap in block"
grep -q '^RAYON_NUM_THREADS=16$' "$_tmpenv" || fail_later "managed-rayon" "RAYON_NUM_THREADS not set to cap"
grep -q '^HF_HUB_DISABLE_XET=1$' "$_tmpenv" || fail_later "managed-xet" "HF_HUB_DISABLE_XET not set to 1"
# Idempotent: a second run (e.g. a restart, or a new host) must not stack blocks.
_vast_write_caps 8 "$_tmpenv"
[[ "$(grep -c 'VAST_CPU_THREAD_CAPS_BEGIN' "$_tmpenv")" == "1" ]] || fail_later "idempotent" "managed block stacked on re-run"
grep -q '^MKL_NUM_THREADS=8$'   "$_tmpenv" || fail_later "recompute" "re-run did not recompute cap (8)"
grep -q '^OMP_NUM_THREADS=99$'  "$_tmpenv" || fail_later "override-persist" "user OMP lost on re-run"
rm -f "$_tmpenv"

# ── 3. Live consistency: /etc/environment matches the recomputed decision ──
_pm=$(_vast_read_pids_max); _vis=$(nproc 2>/dev/null); _q=$(_vast_read_quota_cores)
_cap=$(_vast_thread_cap "${_pm:-}" "${_vis:-}" "${_q:-}")
echo "  live: pids.max=${_pm:-?} nproc=${_vis:-?} quota=${_q:-none} -> cap=${_cap:-<no-op>}"
if [[ -z "$_cap" ]]; then
    # Healthy host: the hook must not have written a managed block.
    if grep -qF "VAST_CPU_THREAD_CAPS_BEGIN" /etc/environment 2>/dev/null; then
        fail_later "healthy-host" "managed block present on a host that should be a no-op"
    fi
else
    # Pathological host: block present and each unmanaged var at the cap.
    grep -qF "VAST_CPU_THREAD_CAPS_BEGIN" /etc/environment 2>/dev/null \
        || fail_later "patho-host" "expected a managed block (cap=${_cap}) but none in /etc/environment"
    [[ "${OMP_NUM_THREADS:-}" == "$_cap" ]] \
        || fail_later "patho-omp" "OMP_NUM_THREADS='${OMP_NUM_THREADS:-}' != cap ${_cap}"
fi

report_failures
test_pass "cpu thread caps: decision, override-respect, and live state verified"
