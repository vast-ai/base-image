#!/bin/bash
# On-box test driver, delivered over SSH by tools/template_manager (ADR 0068).
#
# This is the baked runner (ROOT/opt/instance-tools/tests/runner.sh) without its
# results server, wait-for-client, token guard, webhook and closing sleep. Discovery,
# the phase gate, exit-code meanings, per-test TEST_TIMEOUT, the required-pass gate and
# every line of per-test output are the runner's, unchanged: the launcher's stream
# parser reads this output exactly as it reads the runner's.
#
# It lives in <root>/<run-id>/bundle/ beside lib.sh and the suites, and writes to
# <root>/<run-id>/:
#   pid          this process, so the client can tell a dead driver from a quiet one
#   output.log   what the runner writes to its output log
#   results.json the runner's results file, same shape
#   started      written once the lock and the environment are held
#   done         written last: "<run-id> <state>"
#
# Two stages. Stage one runs in whatever environment SSH gave it: it takes the lock,
# waits for supervisord and re-executes itself with exactly supervisord's environment
# (minus `_`). Stage two is the runner's main loop. Boot stages export only before
# supervisord starts and the baked runner is started after it from the same shell, so
# that is the environment the baked runner inherits; the SSH login environment is built
# by a different parser and misses values the boot exports.
#
# Usage:
#   driver.sh                    one run, writing to <root>/<run-id>/
#   driver.sh --manual           the same run in the foreground, for the fix loop;
#                                writes to <root>/<run-id>/manual/ so the delivered
#                                run's record is kept
#   driver.sh --only <names>     as --manual, running only the named tests
#                                (comma- or space-separated, e.g. base/10-supervisor)
#   --supervisord-pidfile PATH   default /var/run/supervisord.pid
#   --supervisord-wait SECONDS   default 900

set -euo pipefail

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_DIR="$(dirname "$TESTS_DIR")"
RUN_ID="$(basename "$RUN_DIR")"
LOCK_FILE="$(dirname "$RUN_DIR")/lock"

STAGE=one
MANUAL=false
ONLY=""
SUPERVISORD_PIDFILE="/var/run/supervisord.pid"
SUPERVISORD_WAIT=900
while (( $# )); do
    case "$1" in
        --run) STAGE=two ;;
        --manual) MANUAL=true ;;
        --only) MANUAL=true; ONLY="${2:?--only needs test names}"; shift ;;
        --supervisord-pidfile) SUPERVISORD_PIDFILE="${2:?--supervisord-pidfile needs a path}"; shift ;;
        --supervisord-wait) SUPERVISORD_WAIT="${2:?--supervisord-wait needs seconds}"; shift ;;
        *) echo "driver.sh: unknown argument: $1" >&2; exit 64 ;;
    esac
    shift
done

if [[ "$MANUAL" == "true" ]]; then
    OUT_DIR="${RUN_DIR}/manual"
else
    OUT_DIR="$RUN_DIR"
fi
RESULTS_FILE="${OUT_DIR}/results.json"
OUTPUT_LOG="${OUT_DIR}/output.log"
DEFAULT_TEST_TIMEOUT="${INSTANCE_TEST_DEFAULT_TIMEOUT:-3600}"  # 1 hour; override per-test with # TEST_TIMEOUT=N
START_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
START_EPOCH=$(date +%s)

# log_output: write to both stdout and the log file
log_output() {
    while IFS= read -r line; do
        printf '%s\n' "$line"
        printf '%s\n' "$line" >> "$OUTPUT_LOG"
    done
}

# The completion marker. Written last and atomically: the client ends the run on it.
write_done() {
    printf '%s %s\n' "$RUN_ID" "$1" > "${OUT_DIR}/done.tmp"
    mv "${OUT_DIR}/done.tmp" "${OUT_DIR}/done"
}

# ── Stage one: lock, wait for supervisord, take its environment ──────

supervisord_pid() {
    local pid
    pid=$(cat "$SUPERVISORD_PIDFILE" 2>/dev/null) || return 1
    [[ "$pid" =~ ^[0-9]+$ && -r "/proc/${pid}/cmdline" ]] || return 1
    # The pid file outlives a dead supervisord, and a pid is reused: the command line
    # has to name it.
    tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -q supervisord || return 1
    printf '%s' "$pid"
}

if [[ "$STAGE" == "one" ]]; then
    mkdir -p "$OUT_DIR"
    echo "$$" > "${OUT_DIR}/pid"
    : > "$OUTPUT_LOG"

    # One run at a time. The lock is held on fd 9 until this process exits; stage two
    # inherits it across the exec.
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo "Another test run holds ${LOCK_FILE}; this one was not started" | log_output
        write_done "busy"
        exit 75
    fi

    waited=0
    announced=false
    until sup_pid=$(supervisord_pid); do
        if (( waited >= SUPERVISORD_WAIT )); then
            echo "supervisord did not start within ${SUPERVISORD_WAIT}s (${SUPERVISORD_PIDFILE});" \
                 "its environment is the one the checks run in, so nothing was run" | log_output
            write_done "environment"
            exit 1
        fi
        if [[ "$announced" == "false" ]]; then
            echo "Waiting for supervisord (up to ${SUPERVISORD_WAIT}s)..." | log_output
            announced=true
        fi
        sleep 1
        waited=$((waited + 1))
    done

    # Exactly supervisord's environment, minus `_` (the path of whatever started it).
    # Passed through env(1), not exported here: a name bash cannot hold as a variable
    # still reaches the checks.
    sup_env=()
    while IFS= read -r -d '' entry; do
        [[ "$entry" == *=* && "$entry" != _=* ]] && sup_env+=("$entry")
    done < "/proc/${sup_pid}/environ"

    stage_two=(bash "${TESTS_DIR}/driver.sh" --run)
    [[ "$MANUAL" == "true" ]] && stage_two+=(--manual)
    [[ -n "$ONLY" ]] && stage_two+=(--only "$ONLY")
    # Not the run directory: it belongs to the client, which may remove it.
    cd /
    exec env -i -- "${sup_env[@]}" "${stage_two[@]}"
fi

# ── Stage two: the runner's main loop ────────────────────────────────

# The boot shell's umask, set explicitly: sshd's is not the same.
umask 0002

# The boot shell's open-file limit: 1048576, or the hard limit if that is lower.
_nofile=1048576
_hard=$(ulimit -Hn)
[[ "$_hard" != "unlimited" ]] && (( _hard < _nofile )) && _nofile=$_hard
ulimit -Sn "$_nofile" 2>/dev/null || true

: > "${OUT_DIR}/started"

# ── JSON helpers (no jq dependency) ──────────────────────────────────

_json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

write_results() {
    local state="$1"
    local now_epoch
    now_epoch=$(date +%s)
    local elapsed=$((now_epoch - START_EPOCH))

    local tests_json=""
    # `local idx`, NOT a bare `i`: this function is called from inside the runner's
    # own `for i in "${!ALL_TESTS[@]}"` loop, and bash has no function-local scope
    # unless asked for one. A bare `i` here left the caller's index pointing at the
    # LAST test, so every per-test state was written to the wrong slot — each test
    # finished by marking the final entry, and every earlier entry stayed "running"
    # even on a clean pass.
    local idx
    for idx in "${!TEST_NAMES[@]}"; do
        [[ -n "$tests_json" ]] && tests_json+=","
        tests_json+=$(printf '{"name":"%s","state":"%s","duration_s":%s}' \
            "$(_json_escape "${TEST_NAMES[$idx]}")" \
            "${TEST_STATES[$idx]}" \
            "${TEST_DURATIONS[$idx]}")
    done

    local json
    json=$(printf '{"state":"%s","started_at":"%s","elapsed_s":%d,"tests":[%s]}' \
        "$state" "$START_TIME" "$elapsed" "$tests_json")

    # Atomic write
    printf '%s\n' "$json" > "${RESULTS_FILE}.tmp"
    mv "${RESULTS_FILE}.tmp" "$RESULTS_FILE"
}

# ── Test discovery ───────────────────────────────────────────────────

discover_tests() {
    local tests=()

    # Base tests (always present)
    if [[ -d "${TESTS_DIR}/base" ]]; then
        while IFS= read -r f; do
            tests+=("$f")
        done < <(find "${TESTS_DIR}/base" -name '*.sh' -executable | sort)
    fi

    # Derivative tests (*.d/ directories)
    while IFS= read -r d; do
        while IFS= read -r f; do
            tests+=("$f")
        done < <(find "$d" -name '*.sh' -executable | sort)
    done < <(find "${TESTS_DIR}" -maxdepth 1 -name '*.d' -type d | sort)

    # Guard the empty case explicitly: `printf '%s\n' "${empty[@]}"` emits one BLANK
    # LINE, not nothing, so the caller's `mapfile` yields a one-element array holding ""
    # and the zero-test guard downstream is unreachable — the runner would log
    # "Discovered 1 tests" and then exec `bash ""`, which exits 127.
    (( ${#tests[@]} )) || return 0
    printf '%s\n' "${tests[@]}"
}

# ── Main ─────────────────────────────────────────────────────────────

base_failed=false
declare -a TEST_NAMES=()
declare -a TEST_STATES=()
declare -a TEST_DURATIONS=()

# Discover tests
mapfile -t ALL_TESTS < <(discover_tests)
# Drop any empty element regardless of what the producer did — a blank path silently
# becomes `bash ""` (exit 127) attributed to a test that does not exist.
_kept=(); for _t in "${ALL_TESTS[@]}"; do [[ -n "$_t" ]] && _kept+=("$_t"); done
ALL_TESTS=("${_kept[@]}")

# --only: keep the named tests, in discovery order. A name that matches nothing is an
# error, never an empty run.
if [[ -n "$ONLY" ]]; then
    read -ra _only_names <<< "${ONLY//,/ }"
    _kept=()
    for _t in "${ALL_TESTS[@]}"; do
        _name="${_t#"${TESTS_DIR}/"}"
        _name="${_name%.sh}"
        for _want in "${_only_names[@]}"; do
            [[ "$_want" == "$_name" ]] && _kept+=("$_t")
        done
    done
    for _want in "${_only_names[@]}"; do
        _found=false
        for _t in "${_kept[@]}"; do
            [[ "$_t" == "${TESTS_DIR}/${_want}.sh" ]] && _found=true
        done
        if ! $_found; then
            echo "--only: no test named ${_want} in ${TESTS_DIR}" | log_output
            write_results "failed"
            write_done "failed"
            exit 1
        fi
    done
    ALL_TESTS=("${_kept[@]}")
fi

if [[ ${#ALL_TESTS[@]} -eq 0 ]]; then
    # NOT "passed". This branch is upstream of the INSTANCE_TEST_REQUIRE_PASS check, and
    # test_template.py trusts the verdict it reports — so a suite that discovered nothing
    # would certify an image it never touched. Discovering zero tests inside an image
    # that ships base/*.sh means the suite itself is broken (a bad TESTS_DIR, a lost
    # executable bit), which is a failure, not a pass.
    echo "No tests found in ${TESTS_DIR} — the suite is broken, not empty" | log_output
    write_results "failed"
    write_done "failed"
    exit 1
fi

echo "Discovered ${#ALL_TESTS[@]} tests" | log_output

# Initialize all tests as pending
for test_path in "${ALL_TESTS[@]}"; do
    local_name="${test_path#"${TESTS_DIR}/"}"
    local_name="${local_name%.sh}"
    TEST_NAMES+=("$local_name")
    TEST_STATES+=("pending")
    TEST_DURATIONS+=("0")
done

write_results "running"

# Run tests
has_failure=false
for i in "${!ALL_TESTS[@]}"; do
    test_path="${ALL_TESTS[$i]}"
    test_name="${TEST_NAMES[$i]}"

    # ── Phase gate: do not start the expensive tail on a sick platform ──
    #
    # base/ is the platform contract and is cheap — the whole phase is ~60s.
    # The derivative *.d/ suites are the expensive part: model downloads and
    # inference, tens of minutes on an image like comfyui or vllm.
    #
    # If base failed, this cell is already lost. ADR 0029 redraws on ANY
    # failure, so nothing downstream can change the verdict — it can only spend
    # a rented GPU for tens of minutes to reach a conclusion already reached,
    # and then be discarded. Worse, every derivative assertion after a broken
    # base is measuring a degraded platform, so a PASS there is not evidence
    # either.
    #
    # The cut is at the PHASE boundary, deliberately, not at the failing test.
    # Aborting on the first red would have destroyed the evidence that produced
    # ADR 0029's amendment: 10-supervisor, 20-portal and 26-caddy-auth failing
    # TOGETHER is what identified the shared root cause, and 67-service-
    # functionality passing on the same endpoints 53s later is what proved the
    # host innocent. Cheap diagnostics are worth finishing; the expensive tail
    # is not. Same reasoning as 12-provisioning's existing test_fatal.
    if [[ "$test_name" != base/* && "$base_failed" == "true" ]]; then
        for ((j=i; j<${#ALL_TESTS[@]}; j++)); do
            TEST_STATES[$j]="skipped"
        done
        echo "" | log_output
        echo "─── DERIVATIVE PHASE NOT ATTEMPTED ───" | log_output
        echo "  A base/ test failed, so the ${test_name%%/*} suite was not run." | log_output
        echo "  These tests are NOT passing and NOT skipped-by-design — they were" | log_output
        echo "  never started. The cell fails on the base failure above and is" | log_output
        echo "  redrawn (ADR 0029) without spending the derivative phase." | log_output
        write_results "running"
        break
    fi

    echo "─── Running: ${test_name} ───" | log_output
    TEST_STATES[$i]="running"
    write_results "running"

    test_start=$(date +%s)
    # Per-test timeout: check for TEST_TIMEOUT in script header, fall back to DEFAULT_TEST_TIMEOUT.
    test_timeout=$(grep -oP '^# TEST_TIMEOUT=\K\d+' "$test_path" 2>/dev/null || echo "$DEFAULT_TEST_TIMEOUT")
    set +e
    # Run test with clean shell options — don't leak errexit/nounset via SHELLOPTS.
    # 9>&-: a process a test leaves behind must not keep the run lock held.
    TEST_NAME="$test_name" timeout "$test_timeout" env -u SHELLOPTS bash "$test_path" 2>&1 9>&- | log_output
    rc=${PIPESTATUS[0]}
    set -e
    test_end=$(date +%s)
    TEST_DURATIONS[$i]=$((test_end - test_start))

    case $rc in
        0)
            TEST_STATES[$i]="passed"
            echo "  → PASSED (${TEST_DURATIONS[$i]}s)" | log_output
            ;;
        2)
            # Fatal: test signalled the suite should abort (e.g. provisioning failed)
            TEST_STATES[$i]="failed"
            has_failure=true
            [[ "$test_name" == base/* ]] && base_failed=true
            echo "  → FAILED [FATAL] (${TEST_DURATIONS[$i]}s)" | log_output
            # Mark remaining tests as skipped
            for ((j=i+1; j<${#ALL_TESTS[@]}; j++)); do
                TEST_STATES[$j]="skipped"
            done
            echo "  aborting suite — fatal failure in ${test_name}" | log_output
            write_results "running"
            break
            ;;
        77)
            TEST_STATES[$i]="skipped"
            echo "  → SKIPPED (${TEST_DURATIONS[$i]}s)" | log_output
            ;;
        124)
            TEST_STATES[$i]="failed"
            has_failure=true
            [[ "$test_name" == base/* ]] && base_failed=true
            echo "  → FAILED (timeout after ${test_timeout}s)" | log_output
            ;;
        *)
            TEST_STATES[$i]="failed"
            has_failure=true
            [[ "$test_name" == base/* ]] && base_failed=true
            echo "  → FAILED (exit code ${rc}, ${TEST_DURATIONS[$i]}s)" | log_output
            ;;
    esac

    write_results "running"
done

# Required-pass gate (ADR 0019). A QA template names the tests that MUST have
# PASSED — not skipped, not missing from the image, not left unreached by an
# earlier fatal. Without this, a test that self-skips is indistinguishable from
# one that ran clean: the GPU suite skips itself on a box where nvidia-smi or
# libcuda is unavailable, and the runner reports the whole suite green, so a QA
# gate could certify an image whose CUDA userland never loaded.
#
# Opt-in by design: unset (every customer instance, and any image not being
# gated on it) keeps the existing skip-is-fine behaviour.
if [[ -n "${INSTANCE_TEST_REQUIRE_PASS:-}" ]]; then
    echo "─── Required-pass gate: ${INSTANCE_TEST_REQUIRE_PASS} ───" | log_output
    # Accept comma- and/or whitespace-separated names.
    # read -ra, not an unquoted expansion: word-splitting is wanted, pathname
    # expansion is not. An unquoted $_required_raw containing a `*` would glob
    # against the CWD and silently rewrite the required list.
    read -ra _required_names <<< "${INSTANCE_TEST_REQUIRE_PASS//,/ }"
    for _req in "${_required_names[@]}"; do
        [[ -z "$_req" ]] && continue
        _found=false
        _state=""
        # `_idx`, not `i` — the runner's test loop owns `i` at top level (see the
        # note in write_results); reusing it here would work only by accident of
        # this block running last.
        for _idx in "${!TEST_NAMES[@]}"; do
            if [[ "${TEST_NAMES[$_idx]}" == "$_req" ]]; then
                _found=true
                _state="${TEST_STATES[$_idx]}"
                break
            fi
        done
        # NOTE: deliberately not prefixed with the "→" verdict marker. The client
        # (test_template.py) attributes any line starting with → to the test named
        # by the preceding "─── Running: ─── " header, and by this point that is
        # cleared — such a line would bump the failed counter while attaching to no
        # test. The gate's effect reaches the client through has_failure, which
        # makes the final result event "failed"; that is the authoritative path.
        if ! $_found; then
            has_failure=true
            echo "  REQUIRED-FAIL ${_req}: missing from this image" | log_output
        elif [[ "$_state" != "passed" ]]; then
            has_failure=true
            echo "  REQUIRED-FAIL ${_req}: did not pass (state=${_state})" | log_output
        else
            echo "  ok: ${_req}" | log_output
        fi
    done
fi

# Final state
if $has_failure; then
    write_results "failed"
    echo "══════════════════════════════" | log_output
    echo "  TEST SUITE FAILED" | log_output
    echo "══════════════════════════════" | log_output
else
    write_results "passed"
    echo "══════════════════════════════" | log_output
    echo "  ALL TESTS PASSED" | log_output
    echo "══════════════════════════════" | log_output
fi

if $has_failure; then
    write_done "failed"
    exit 1
fi
write_done "passed"
exit 0
