#!/bin/bash
# Test helper library — sourced by individual test scripts.
#
# Provides:
#   test_pass "message"   — report success
#   test_fail "message"   — report failure (exits 1)
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
#   portal_has_entry — predicate (return 0/1)

TEST_NAME="${TEST_NAME:-$(basename "$0" .sh)}"

test_pass() {
    echo "PASS: ${TEST_NAME}: ${1:-ok}"
    exit 0
}

test_fail() {
    echo "FAIL: ${TEST_NAME}: ${1:-assertion failed}"
    exit 1
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

portal_has_entry() {
    [[ -f /etc/portal.yaml ]] && grep -qiE "^[^#].*${1}" /etc/portal.yaml
}

# ── Wait helpers ─────────────────────────────────────────────────────

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
