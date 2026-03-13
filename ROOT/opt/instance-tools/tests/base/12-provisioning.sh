#!/bin/bash
# TEST_TIMEOUT=3900
# Test: provisioning completes successfully (or is not configured).
#
# This test actively monitors the provisioning process for signs of life.
# If provisioning appears hung (no log growth, no provisioner children, no disk
# activity) for PROV_STALL_TIMEOUT seconds, it fails.
# Total provisioning time is capped at PROV_TIMEOUT seconds.
#
# This test blocks until provisioning finishes, so subsequent tests can assume
# provisioning is done.
source "$(dirname "$0")/../lib.sh"

PROV_LOG="/var/log/portal/provisioning.log"
PROV_STALL_TIMEOUT="${PROV_STALL_TIMEOUT:-180}"   # 3 min with no activity = hung
PROV_TIMEOUT="${PROV_TIMEOUT:-3600}"               # 1 hour total max
POLL_INTERVAL=5

# ── Is provisioning even configured? ─────────────────────────────────

provisioning_configured() {
    [[ -n "${PROVISIONING_MANIFEST:-}" ]] || [[ -f /provisioning.yaml ]] || [[ -n "${PROVISIONING_SCRIPT:-}" ]]
}

# ── Already done? ────────────────────────────────────────────────────

if [[ -f /.provisioning_complete ]]; then
    echo "  provisioning already complete"
    test_pass "provisioning complete"
fi

if [[ -f /.provisioning_failed ]]; then
    echo "  provisioning failed (/.provisioning_failed exists)"
    # Check if the log has useful info
    if [[ -f "$PROV_LOG" ]]; then
        echo "  last 5 lines of provisioning log:"
        tail -5 "$PROV_LOG" | sed 's/^/    /'
    fi
    test_fatal "provisioning failed"
fi

# Not in progress and not configured — nothing to do
if [[ ! -f /.provisioning ]]; then
    if provisioning_configured; then
        # Configured but no marker and no outcome — the boot sequence may not
        # have reached step 65 yet, or 95 already cleaned up without provisioner running.
        # Either way, nothing to monitor.
        echo "  provisioning configured but /.provisioning marker absent"
    fi
    test_pass "no provisioning in progress"
fi

# ── Provisioning is in progress — monitor it ─────────────────────────

echo "  /.provisioning exists — monitoring provisioning process"

# Get initial state for activity detection
last_activity=$(date +%s)

get_log_size() {
    stat -c '%s' "$PROV_LOG" 2>/dev/null || echo "0"
}

get_log_mtime() {
    stat -c '%Y' "$PROV_LOG" 2>/dev/null || echo "0"
}

# Count descendant processes of the provisioner (pip, git, apt, curl, wget, etc.)
provisioner_children() {
    local prov_pid
    prov_pid=$(pgrep -f "provisioner" 2>/dev/null | head -1)
    if [[ -n "$prov_pid" ]]; then
        # Count all descendants
        pgrep -P "$prov_pid" 2>/dev/null | wc -l
    else
        echo "0"
    fi
}

# Check for any common provisioning-related processes
active_provisioning_processes() {
    pgrep -af "(pip|uv pip|git clone|apt|wget|curl|conda|provisioner)" 2>/dev/null | grep -cv "pgrep" || echo "0"
}

prev_log_size=$(get_log_size)
prev_log_mtime=$(get_log_mtime)
start_time=$(date +%s)

while [[ -f /.provisioning ]]; do
    sleep "$POLL_INTERVAL"

    now=$(date +%s)
    elapsed=$((now - start_time))

    # Check total timeout
    if [[ $elapsed -ge $PROV_TIMEOUT ]]; then
        echo "  provisioning exceeded total timeout of ${PROV_TIMEOUT}s"
        if [[ -f "$PROV_LOG" ]]; then
            echo "  last 10 lines of provisioning log:"
            tail -10 "$PROV_LOG" | sed 's/^/    /'
        fi
        test_fatal "provisioning timed out after ${PROV_TIMEOUT}s"
    fi

    # Detect activity: log growth, log mtime change, or active processes
    cur_log_size=$(get_log_size)
    cur_log_mtime=$(get_log_mtime)
    active_procs=$(active_provisioning_processes)

    activity_detected=false

    if [[ "$cur_log_size" != "$prev_log_size" ]]; then
        activity_detected=true
    fi
    if [[ "$cur_log_mtime" != "$prev_log_mtime" ]]; then
        activity_detected=true
    fi
    if [[ "$active_procs" -gt 0 ]]; then
        activity_detected=true
    fi

    if $activity_detected; then
        last_activity=$now
        prev_log_size=$cur_log_size
        prev_log_mtime=$cur_log_mtime
    fi

    stall_duration=$((now - last_activity))

    # Progress report every 30s
    if (( elapsed % 30 == 0 )); then
        echo "  [${elapsed}s] log=${cur_log_size}B procs=${active_procs} stall=${stall_duration}s"
    fi

    # Check stall timeout
    if [[ $stall_duration -ge $PROV_STALL_TIMEOUT ]]; then
        echo "  no provisioning activity for ${stall_duration}s (threshold: ${PROV_STALL_TIMEOUT}s)"
        echo "  provisioner PID: $(pgrep -f provisioner 2>/dev/null || echo 'none')"
        echo "  active provisioning processes: ${active_procs}"
        if [[ -f "$PROV_LOG" ]]; then
            echo "  last 10 lines of provisioning log:"
            tail -10 "$PROV_LOG" | sed 's/^/    /'
        fi
        test_fatal "provisioning appears hung (no activity for ${stall_duration}s)"
    fi
done

# ── Provisioning finished — check outcome ────────────────────────────

elapsed=$(($(date +%s) - start_time))
echo "  provisioning finished after ${elapsed}s"

if [[ -f /.provisioning_complete ]]; then
    echo "  outcome: success"
    if [[ -f "$PROV_LOG" ]]; then
        echo "  last line: $(tail -1 "$PROV_LOG")"
    fi
    # Brief stabilization for services that start after provisioning
    sleep 5
    test_pass "provisioning completed successfully in ${elapsed}s"
fi

if [[ -f /.provisioning_failed ]]; then
    echo "  outcome: failed"
    if [[ -f "$PROV_LOG" ]]; then
        echo "  last 5 lines of provisioning log:"
        tail -5 "$PROV_LOG" | sed 's/^/    /'
    fi
    test_fatal "provisioning failed after ${elapsed}s"
fi

# /.provisioning gone but no outcome marker — the 95-supervisor-wait.sh removed it
# without the provisioner having run (no provisioning configured), or the provisioner
# was skipped because /.provisioning_complete already existed.
echo "  /.provisioning removed, no outcome marker (provisioning may not have been configured)"
test_pass "provisioning phase complete (${elapsed}s)"
