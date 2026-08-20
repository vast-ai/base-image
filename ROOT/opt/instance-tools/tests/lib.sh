#!/bin/bash
# Test helper library — sourced by individual test scripts.
#
# Provides:
#   test_pass "message"   — report success
#   test_fail "message"   — report failure (exits 1)
#   test_fatal "message"  — report failure and abort suite (exits 2)
#   test_skip "message"   — report skip   (exits 77)
#   wait_for_supervisor [timeout_s] — wait for supervisord's RPC socket
#   wait_for_url URL [timeout_s] — wait for HTTP 200
#   wait_for_port PORT [timeout_s] — wait for TCP port to be listening
#   assert_file_exists PATH
#   assert_dir_exists PATH
#   assert_file_mode PATH EXPECTED_OCTAL
#   assert_command_exists CMD
#   assert_service_running NAME [timeout_s]
#   assert_service_stopped NAME
#   assert_env_set VARNAME
#   assert_user_exists USERNAME [UID]
#   has_gpu          — predicate (return 0/1)
#   is_serverless    — predicate (return 0/1)
#   is_vast_image    — predicate (return 0/1): IMAGE_TYPE=vast
#   portal_has_entry — predicate (return 0/1)
#   service_running NAME — predicate (return 0/1)
#   version_gt A B   — predicate: version A > B (dotted integers)
#   find_caddy_ports — populate REPLY with active Caddy external ports
#   caddy_port_proto PORT — echo "https" or "http" for a Caddyfile port
#   http_check LABEL EXPECTED [curl_args...] — HTTP status check via fail_later
#   fail_later LABEL MSG — record failure without exiting (call report_failures at end)
#   report_failures  — exit with failure if any fail_later calls were made

TEST_NAME="${TEST_NAME:-$(basename "$0" .sh)}"

test_pass() {
    echo "PASS: ${TEST_NAME}: ${1:-ok}"
    exit 0
}

test_fail() {
    echo "FAIL: ${TEST_NAME}: ${1:-assertion failed}"
    exit 1
}

test_fatal() {
    echo "FATAL: ${TEST_NAME}: ${1:-fatal failure}"
    exit 2
}

test_skip() {
    echo "SKIP: ${TEST_NAME}: ${1:-skipped}"
    exit 77
}

# ── Readiness budgets ────────────────────────────────────────────────
# These are judgement calls, not structure — see docs/invariants.md. Two things
# follow from that, and both are deliberate:
#
#   1. They are ENV-OVERRIDABLE. The suite ships inside the image, so a number
#      baked here can only be corrected by rebuilding and re-promoting every
#      image. Behind a variable, a wrong budget is a template edit instead.
#   2. Their DEFAULTS are floor-pinned by linter rule L070, against the
#      measurements recorded in docs/invariants.md. A linter cannot decide
#      whether 20 is *enough*; it can decide whether someone has quietly put it
#      back below the cost that was actually measured. Reverting HTTP_CHECK_MAX_TIME
#      to 5 used to pass every test in this repo.
SUPERVISOR_READY_TIMEOUT="${SUPERVISOR_READY_TIMEOUT:-60}"
PORTAL_READY_TIMEOUT="${PORTAL_READY_TIMEOUT:-120}"
CADDY_READY_TIMEOUT="${CADDY_READY_TIMEOUT:-120}"
HTTP_CHECK_MAX_TIME="${HTTP_CHECK_MAX_TIME:-20}"

# ── Predicates (return 0/1, don't exit) ──────────────────────────────

has_gpu() {
    nvidia-smi &>/dev/null
}

is_serverless() {
    local val="${SERVERLESS:-}"
    [[ "${val,,}" == "true" ]]
}

is_vast_image() {
    [[ "${IMAGE_TYPE:-}" == "vast" ]]
}

portal_has_entry() {
    [[ -f /etc/portal.yaml ]] && grep -qiE "^[^#].*${1}" /etc/portal.yaml
}

# Wait until supervisord is SERVING, not merely forked.
#
# 65-supervisor-launch.sh backgrounds supervisord and boot moves straight on to
# 70-instance-test.sh, which backgrounds this suite. The two are effectively
# simultaneous, so the first test runs while supervisord is still starting —
# and `pgrep` is satisfied at fork while the RPC socket every service assertion
# needs appears later. Measured in the shipped image, idle, 16 cores: presence
# at 1.7ms, socket usable at 383ms. On a contended host mid-provisioning that
# gap is seconds, which is how base/10-supervisor failed with
# `supervisorctl cannot communicate with supervisord (exit 4)` 0.09s into a QA
# run and took 20-portal and 26-caddy-auth down as collateral (ADR 0029).
#
# supervisorctl exits 4 on both ECONNREFUSED and ENOENT against the serverurl —
# despite the constant being named INSUFFICIENT_PRIVILEGES, it means "nothing is
# listening on that socket yet". Only 0 and 3 mean we reached it (3 = some
# programs not running, expected this early and not our question here).
#
# NOT `<= 3`: that also accepts 1, supervisorctl's generic error, which covers
# things like EACCES against the chmod=0700 socket. A readiness primitive must
# not treat an unclassified error as ready — that is failing open in the one
# place the whole suite trusts.
#
# WALL CLOCK, not an iteration count. Each poll runs supervisorctl, a Python
# interpreter start, which on the hosts this exists for costs seconds rather
# than milliseconds — so counting sleeps made "60s" mean minutes, which is the
# same defect class (a budget that does not measure what it names) as the one
# this file exists to fix.
#
# Memoised BOTH ways. Success: every service helper calls this, and only the
# first call can be racing anything. Failure: 67-service-functionality makes six
# service_running calls with no gate of its own, and re-proving a dead socket
# each time cost ~360s of rented GPU to rediscover a fact known at second 60.
# The failure memo records how long was already waited, so a later call with a
# LONGER budget is still allowed to try.
_SUPERVISOR_READY=""
_SUPERVISOR_WAITED=0
wait_for_supervisor() {
    [[ -n "$_SUPERVISOR_READY" ]] && return 0
    local timeout="${1:-$SUPERVISOR_READY_TIMEOUT}"
    (( timeout <= _SUPERVISOR_WAITED )) && return 1
    local deadline=$(( SECONDS + timeout ))
    local rc
    while :; do
        supervisorctl status &>/dev/null; rc=$?
        if (( rc == 0 || rc == 3 )); then
            _SUPERVISOR_READY=1
            return 0
        fi
        if (( SECONDS >= deadline )); then
            _SUPERVISOR_WAITED=$timeout
            return 1
        fi
        sleep 1
    done
}

# Status word for a supervisor program, through the socket, once it is up.
# The word is not always a STATE: an unreachable socket yields `no` (from
# "unix:///... no such file") and an unknown program yields `ERROR` (from
# "foo: ERROR (no such process)"). Callers must treat anything that is not a
# real state as "cannot tell", not as a verdict.
# Deliberately NOT self-waiting: it is called inside $( ), where a memo
# assignment would die with the subshell and every lookup would pay the full
# timeout again — eight services at sixty seconds in 65-conditional-services.
# Callers reach wait_for_supervisor in their own shell first.
_service_status() {
    supervisorctl status "$1" 2>/dev/null | awk '{print $2}'
}

service_running() {
    wait_for_supervisor || return 1
    local status
    status=$(_service_status "$1")
    [[ "$status" == "RUNNING" ]]
}

# Compare dotted version strings as integers (e.g. "12.10" > "12.9").
# Returns 0 (true) if $1 > $2, 1 (false) otherwise.
# Non-numeric or empty input returns FALSE rather than crashing. Bash arithmetic
# on a non-numeric word raises "syntax error: operand expected" on stderr and
# yields a non-zero status, so the caller silently got "false" plus a confusing
# error in the log. Observed on a driver-610 host, where the CUDA version could
# not be parsed and a PATH ("/etc/alternatives/cuda") reached this function:
#
#   lib.sh: line 84: ((: /etc/alternatives/cuda: syntax error: operand expected
#
# False is the right answer for "unknown" here — every caller uses this to decide
# whether a NEWER toolkit needs compat, and "we do not know" must not assert that
# it does. Returning it explicitly, without the stderr noise, is the whole fix.
version_gt() {
    [[ "$1" =~ ^[0-9]+(\.[0-9]+)*$ ]] || return 1
    [[ "$2" =~ ^[0-9]+(\.[0-9]+)*$ ]] || return 1
    local IFS=.
    local a=($1) b=($2)
    # A single-component version ("13") has no a[1]; default to 0 so "13" == "13.0".
    (( ${a[0]:-0} > ${b[0]:-0} || (${a[0]:-0} == ${b[0]:-0} && ${a[1]:-0} > ${b[1]:-0}) ))
}

# ── Deferred failure pattern ─────────────────────────────────────────
# Use these when a test needs to check multiple things before exiting.
# Call fail_later() for each sub-check failure, then report_failures at the end.
#
# Usage:
#   FAILURES=()   # (automatically initialized by fail_later if unset)
#   fail_later "label" "expected X, got Y"
#   ...more checks...
#   report_failures

FAILURES=()
fail_later() {
    FAILURES+=("$1")
    echo "  FAIL: $1: $2"
}

report_failures() {
    if [[ ${#FAILURES[@]} -gt 0 ]]; then
        local joined
        joined=$(printf '%s, ' "${FAILURES[@]}")
        test_fail "${#FAILURES[@]} check(s) failed: ${joined%, }"
    fi
}

# ── Instance metadata (written by 11-instance-metadata.sh) ───────────

INSTANCE_METADATA="/tmp/instance-test-metadata.json"

# Read a field from the cached instance metadata JSON.
# Returns empty string if metadata file doesn't exist or field is missing.
instance_field() {
    local field="$1"
    [[ -f "$INSTANCE_METADATA" ]] || return 0
    python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
v = d.get(sys.argv[2], '')
print('' if v is None else v)
" "$INSTANCE_METADATA" "$field" 2>/dev/null
}

# ── Wait helpers ─────────────────────────────────────────────────────

# 120s (CADDY_READY_TIMEOUT), not 30, and a wall-clock deadline.
#
# `supervisorctl restart caddy` returns as soon as the WRAPPER clears
# startsecs=5, but caddy.sh then runs caddy_config_manager.py — which calls
# `caddy hash-password` (bcrypt cost 14) once per proxied app — before `caddy
# run` ever execs. A caddy restart was measured at 43s on a contended QA host,
# against a 30s ceiling here.
#
# That mattered more than a slow test. On expiry this only WARNed and returned 1,
# which every caller ignored; the next http_check then hit a port with no
# listener and recorded `expected 401, got 000` — indistinguishable from the
# bcrypt timeout this change fixes elsewhere, and NOT fixable by a longer
# --max-time, because a refused connection returns instantly. Callers now record
# the expiry as a named failure instead (ADR 0029).
wait_for_caddy() {
    # The port is REQUIRED. It used to default to 2019 — Caddy's admin endpoint,
    # which is enabled by default and binds before the site listeners — so a bare
    # call waited on a port no test probes. L071 blocks the obvious spelling, but
    # `wait_for_caddy ""` and `wait_for_caddy "$unset_var"` reach the same place
    # (${1:-} treats empty as unset) and a regex will not see them. Removing the
    # default makes the defect unrepresentable, which is worth more than the rule.
    local port="${1:?wait_for_caddy needs an explicit port — 2019 is the admin endpoint, not a site}"
    local proto="${2:-http}"
    local timeout="${3:-$CADDY_READY_TIMEOUT}"
    local deadline=$(( SECONDS + timeout ))
    local curl_flags="-sf -o /dev/null --max-time 2"
    [[ "$proto" == "https" ]] && curl_flags+=" -k"
    while :; do
        if curl $curl_flags "${proto}://127.0.0.1:${port}/" 2>/dev/null; then
            return 0
        fi
        # Also accept 401 (auth required) as "responsive"
        local code
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 \
            $([[ "$proto" == "https" ]] && echo "-k") \
            "${proto}://127.0.0.1:${port}/" 2>/dev/null)
        [[ "$code" =~ ^[0-9]+$ && "$code" != "000" ]] && return 0
        (( SECONDS >= deadline )) && break
        sleep 1
    done
    echo "  WARN: caddy not responding on ${proto}://${port} after ${timeout}s"
    return 1
}

# Wait until every port the Caddyfile DECLARES is actually listening.
#
# wait_for_caddy answers for ONE port, and its default is 2019 — Caddy's admin
# endpoint, which is enabled by default (caddy_config_manager.py emits no `admin`
# directive) and comes up while the site listeners are still being provisioned.
# base/26-caddy-auth called it bare, six times, then probed :1111 and :6006. It
# returned success on 2019 with the site ports not yet bound, and the checks
# recorded `expected 401, got 000` — measured on a live QA host, twice on the
# same machine, on an image the same run proved healthy twelve seconds later.
# 27-caddy-tls never had this: it passes "$test_port" explicitly.
#
# The silent variant is worse than the loud one. find_caddy_ports below keys on
# `ss -tln`, so a caddy that has not finished binding yields NO ports and
# 26-caddy-auth takes its `test_skip "no external caddy ports found"` exit — a
# green run that asserted nothing about auth at all.
#
# So the readiness question here is not "is some caddy answering" but "is every
# port this test is about to use bound", and that is answerable from the
# Caddyfile before the listeners exist.
wait_for_caddy_ports() {
    local timeout="${1:-$CADDY_READY_TIMEOUT}"
    local deadline=$(( SECONDS + timeout ))
    local line port missing listeners why
    while :; do
        missing=""
        why=""
        # An ABSENT or EMPTY Caddyfile is "cannot tell", not "ready". caddy.sh
        # rewrites this file as part of the very restart being waited on, and
        # caddy_config_manager.py writes it non-atomically (open-for-write
        # truncates, then `caddy fmt --overwrite` truncates again), so a poll can
        # land on zero bytes. Returning 0 there is the fail-open this helper
        # exists to prevent — and the same read one line later decides whether
        # 26-caddy-auth takes its silent test_skip.
        if [[ ! -s /etc/Caddyfile ]]; then
            why="/etc/Caddyfile is absent or empty"
        else
            # ONE `ss` per poll, not one per port. Two reasons: 240 process
            # spawns per call is silly, and `ss | grep -q` is a SIGPIPE trap —
            # grep exits on the match, ss dies 141, and under `set -o pipefail`
            # a BOUND port would be recorded as missing. No base test sets
            # pipefail today; this stops being true the first time one does.
            listeners=$(ss -tln 2>/dev/null)
            while IFS= read -r line; do
                port=$(echo "$line" | grep -oP ':\K[0-9]+(?= \{)')
                [[ -n "$port" && "$port" != "2019" ]] || continue
                grep -q ":${port} " <<< "$listeners" || { missing="$port"; break; }
            done < <(grep -P '^:\d+ \{' /etc/Caddyfile)
            # Zero declared ports is a LEGITIMATE steady state (an instance with
            # no mapped ports produces no site blocks), which is why emptiness is
            # discriminated above rather than here.
            [[ -z "$missing" ]] && return 0
            why="port ${missing} not listening"
        fi
        if (( SECONDS >= deadline )); then
            echo "  WARN: caddy not ready after ${timeout}s (${why})"
            return 1
        fi
        sleep 1
    done
}

# Populate REPLY array with active external Caddy ports from Caddyfile.
# Excludes admin port (2019), only includes ports with active listeners.
find_caddy_ports() {
    REPLY=()
    [[ -f /etc/Caddyfile ]] || return 0
    local line port
    while IFS= read -r line; do
        port=$(echo "$line" | grep -oP ':\K[0-9]+(?= \{)')
        [[ -n "$port" && "$port" != "2019" ]] || continue
        ss -tln | grep -q ":${port} " && REPLY+=("$port")
    done < <(grep -P '^:\d+ \{' /etc/Caddyfile)
}

# Echo "https" or "http" based on whether the Caddyfile port block has a tls directive.
caddy_port_proto() {
    local p="$1"
    if grep -A5 "^:${p} {" /etc/Caddyfile | grep -q "tls "; then
        echo "https"
    else
        echo "http"
    fi
}

# --max-time on the PROBE, and a wall-clock deadline on the loop.
#
# Without a per-probe limit this cannot honour its own budget: the counter only
# advanced when a probe COMPLETED, so a service that accepts the connection and
# then wedges blocks the first curl forever, the budget never ticks, and the test
# runs to the runner's timeout — reporting `timedout`, which names no failing
# check at all. The common case (nothing listening) returns instantly on
# ECONNREFUSED, which is why it had not bitten yet.
wait_for_url() {
    local url="$1"
    local timeout="${2:-30}"
    local deadline=$(( SECONDS + timeout ))
    while ! curl -sf --max-time 5 -o /dev/null "$url" 2>/dev/null; do
        (( SECONDS >= deadline )) && return 1
        sleep 1
    done
    return 0
}

wait_for_port() {
    local port="$1"
    local timeout="${2:-30}"
    local deadline=$(( SECONDS + timeout ))
    while ! ss -tln | grep -q ":${port} "; do
        (( SECONDS >= deadline )) && return 1
        sleep 1
    done
    return 0
}

# ── Assertions ───────────────────────────────────────────────────────

assert_file_exists() {
    [[ -e "$1" ]] || test_fail "file not found: $1"
}

assert_dir_exists() {
    [[ -d "$1" ]] || test_fail "directory not found: $1"
}

assert_file_mode() {
    local path="$1"
    local expected="$2"
    [[ -e "$path" ]] || test_fail "file not found for mode check: $path"
    local actual
    actual=$(stat -c '%a' "$path")
    [[ "$actual" == "$expected" ]] || test_fail "mode mismatch on $path: expected $expected, got $actual"
}

assert_command_exists() {
    command -v "$1" &>/dev/null || test_fail "command not found: $1"
}

# Wait for a program to reach RUNNING, rather than asking once.
#
# Every conf.d program carries `startsecs=5`, so for the first five seconds of
# its life supervisord reports STARTING and a single-shot check reads that as
# "not running". Same defect one level up from the socket race: the question is
# a readiness question, so it gets a bounded wait. FATAL short-circuits — that
# is terminal, and waiting 60s to report it helps nobody. A healthy service
# returns on the first poll, so this costs nothing in the normal case.
# ONE deadline covers BOTH waits. The socket wait and the RUNNING wait used to
# carry independent counters of the same size, so a documented "60s" ceiling was
# really 2x60 iterations — measured at 9.06s for a declared budget of 5. The
# ceiling now means what it says.
assert_service_running() {
    local name="$1"
    local timeout="${2:-$SUPERVISOR_READY_TIMEOUT}"
    local deadline=$(( SECONDS + timeout ))
    local status
    wait_for_supervisor "$timeout" \
        || test_fail "supervisord RPC socket never came up (${timeout}s) — cannot check ${name}"
    while :; do
        status=$(_service_status "$name")
        [[ "$status" == "RUNNING" ]] && return 0
        # Terminal, or not a question waiting can answer: report it now rather
        # than sleeping out the budget to say the same thing.
        case "$status" in
            FATAL) test_fail "service entered FATAL and will not start: $name" ;;
            ERROR) test_fail "supervisord does not know a program named: $name" ;;
        esac
        (( SECONDS >= deadline )) && \
            test_fail "service not running after ${timeout}s: $name (status: ${status:-unknown})"
        sleep 1
    done
}

# No wait here, deliberately: this asserts a service is DOWN, and a bounded wait
# for a negative would just be a slower way to reach the same answer. It still
# goes through wait_for_supervisor, because an unreachable socket yields an empty
# status and empty must not read as "stopped".
assert_service_stopped() {
    local name="$1"
    local status
    wait_for_supervisor \
        || test_fail "supervisord RPC socket never came up — cannot check ${name}"
    status=$(_service_status "$name")
    case "$status" in
        STOPPED|EXITED|FATAL) return 0 ;;
        *) test_fail "service not stopped: $name (status: ${status:-unknown})" ;;
    esac
}

assert_env_set() {
    local varname="$1"
    [[ -n "${!varname:-}" ]] || test_fail "env var not set: $varname"
}

assert_user_exists() {
    local username="$1"
    local expected_uid="${2:-}"
    local actual_uid
    actual_uid=$(id -u "$username" 2>/dev/null) || test_fail "user not found: $username"
    if [[ -n "$expected_uid" ]]; then
        [[ "$actual_uid" == "$expected_uid" ]] || test_fail "user $username UID mismatch: expected $expected_uid, got $actual_uid"
    fi
}

# ── HTTP check helper ───────────────────────────────────────────────
# Usage: http_check LABEL EXPECTED_STATUS [curl_args...]
# EXPECTED_STATUS supports pipe-delimited codes, e.g. "200|302".
# Callers pass any extra curl flags (e.g. -k for TLS) in curl_args.

# HTTP_CHECK_MAX_TIME defaults to 20, not 5, because of what this image
# deliberately makes slow. It is a lever (see Readiness budgets above) and its
# default is floor-pinned by L070.
#
# `caddy hash-password` emits bcrypt at cost **14**, and Caddy verifies an
# unknown username against a fake hash anyway so that timing cannot be used to
# enumerate accounts. It caches SUCCESSES only, so every distinct WRONG
# credential — exactly what the auth-rejection checks send — pays a full cost-14
# verification. bcrypt is single-threaded, so the cost scales with the CPU share
# the container actually gets. Measured in the shipped image:
#
#     16 cores idle   690 ms      --cpus=0.25   2232 ms
#     --cpus=0.50    1410 ms      --cpus=0.12   4666 ms
#
# The old 5s budget sat inside that range, so on a contended QA host two
# rejection checks returned `000` (curl timeout, not a server answer) and failed
# a base test on a healthy image. 20s covers roughly a 0.03-core share.
#
# The old budget was also self-amplifying, which is why the production failure
# came in pairs. When curl gives up, Caddy does NOT: it carries on computing the
# bcrypt for the abandoned request, on the same starved core the next check needs.
# Reproduced at --cpus=0.12 — forcing the first check back to 5s made the SECOND
# one fail too, at 20s, and reproduced the production line verbatim:
# `2 check(s) failed: ... expected 401, got 000`. At 20s throughout, 2/2 clean.
#
# Raising it does not slow the common failure down: a service that is DOWN
# refuses the connection and curl returns immediately. This budget only applies
# when something accepted the connection and is still thinking — which here is
# usually crypto doing its job.
#
# Callers can still shorten or lengthen it: curl takes the LAST --max-time, and
# "$@" is appended after this one.
http_check() {
    local label="$1"
    local expected="$2"
    shift 2
    local status
    status=$(curl -s --max-time "$HTTP_CHECK_MAX_TIME" -o /dev/null -w '%{http_code}' "$@" 2>/dev/null)
    if [[ "$expected" == *"|"* ]]; then
        if echo "$expected" | tr '|' '\n' | grep -qx "$status"; then
            echo "  ${label} → ${status} ok"
            return 0
        fi
    elif [[ "$status" == "$expected" ]]; then
        echo "  ${label} → ${status} ok"
        return 0
    fi
    fail_later "$label" "expected ${expected}, got ${status}"
    return 1
}
