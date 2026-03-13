#!/bin/bash
# Test helper library — sourced by individual test scripts.
#
# Provides:
#   test_pass "message"   — report success
#   test_fail "message"   — report failure (exits 1)
#   test_fatal "message"  — report failure and abort suite (exits 2)
#   test_skip "message"   — report skip   (exits 77)
#   wait_for_url URL [timeout_s] — wait for HTTP 200
#   wait_for_port PORT [timeout_s] — wait for TCP port to be listening
#   assert_file_exists PATH
#   assert_dir_exists PATH
#   assert_file_mode PATH EXPECTED_OCTAL
#   assert_command_exists CMD
#   assert_service_running NAME
#   assert_service_stopped NAME
#   assert_env_set VARNAME
#   assert_user_exists USERNAME [UID]
#   has_gpu          — predicate (return 0/1)
#   is_serverless    — predicate (return 0/1)
#   is_vast_image    — predicate (return 0/1): IMAGE_TYPE=vast
#   portal_has_entry — predicate (return 0/1)
#   version_gt A B   — predicate: version A > B (dotted integers)
#   fail_later MSG   — record failure without exiting (call report_failures at end)
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

# ── Predicates (return 0/1, don't exit) ──────────────────────────────

has_gpu() {
    nvidia-smi &>/dev/null
}

is_serverless() {
    [[ "${SERVERLESS,,}" == "true" ]]
}

is_vast_image() {
    [[ "${IMAGE_TYPE:-}" == "vast" ]]
}

portal_has_entry() {
    [[ -f /etc/portal.yaml ]] && grep -qiE "^[^#].*${1}" /etc/portal.yaml
}

# Compare dotted version strings as integers (e.g. "12.10" > "12.9").
# Returns 0 (true) if $1 > $2, 1 (false) otherwise.
version_gt() {
    local IFS=.
    local a=($1) b=($2)
    (( a[0] > b[0] || (a[0] == b[0] && a[1] > b[1]) ))
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

wait_for_caddy() {
    local port="${1:-2019}"  # default: caddy admin API (always available)
    local proto="${2:-http}"
    local curl_flags="-sf -o /dev/null --max-time 2"
    [[ "$proto" == "https" ]] && curl_flags+=" -k"
    for _ in $(seq 1 30); do
        if curl $curl_flags "${proto}://127.0.0.1:${port}/" 2>/dev/null; then
            return 0
        fi
        # Also accept 401 (auth required) as "responsive"
        local code
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 \
            $([[ "$proto" == "https" ]] && echo "-k") \
            "${proto}://127.0.0.1:${port}/" 2>/dev/null)
        [[ "$code" =~ ^[0-9]+$ && "$code" != "000" ]] && return 0
        sleep 1
    done
    echo "  WARN: caddy not responding on ${proto}://${port} after 30s"
    return 1
}

wait_for_url() {
    local url="$1"
    local timeout="${2:-30}"
    local elapsed=0
    while ! curl -sf -o /dev/null "$url" 2>/dev/null; do
        sleep 1
        elapsed=$((elapsed + 1))
        if [[ $elapsed -ge $timeout ]]; then
            return 1
        fi
    done
    return 0
}

wait_for_port() {
    local port="$1"
    local timeout="${2:-30}"
    local elapsed=0
    while ! ss -tln | grep -q ":${port} "; do
        sleep 1
        elapsed=$((elapsed + 1))
        if [[ $elapsed -ge $timeout ]]; then
            return 1
        fi
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

assert_service_running() {
    local name="$1"
    local status
    status=$(supervisorctl status "$name" 2>/dev/null | awk '{print $2}')
    [[ "$status" == "RUNNING" ]] || test_fail "service not running: $name (status: ${status:-unknown})"
}

assert_service_stopped() {
    local name="$1"
    local status
    status=$(supervisorctl status "$name" 2>/dev/null | awk '{print $2}')
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
