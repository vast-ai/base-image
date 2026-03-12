#!/bin/bash
# Instance test runner — discovers and executes test scripts, writes JSON results.
#
# Test discovery order:
#   1. base/*.sh    — base image tests (always present)
#   2. *.d/*.sh     — derivative image tests (dropped by derivative images)
#
# Each test script must exit: 0 (pass), 1 (fail), 77 (skip).
#
# Results are written to RESULTS_FILE as JSON, updated after each test.
# The runner itself exits 0 if all tests pass, 1 if any fail.
#
# Usage:
#   /opt/instance-tools/tests/runner.sh           # automated (from boot script)
#   /opt/instance-tools/tests/runner.sh --manual   # interactive SSH use

set -euo pipefail

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_FILE="${INSTANCE_TEST_RESULTS:-/var/log/test-results.json}"
RESULTS_PORT="${INSTANCE_TEST_PORT:-10199}"
START_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
START_EPOCH=$(date +%s)

# ── Manual mode detection ────────────────────────────────────────────

MANUAL=false
if [[ "${1:-}" == "--manual" ]]; then
    MANUAL=true
    echo "Running in manual mode (no HTTP server, no webhook, no instance stop)"
fi

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
    for i in "${!TEST_NAMES[@]}"; do
        [[ -n "$tests_json" ]] && tests_json+=","
        tests_json+=$(printf '{"name":"%s","state":"%s","duration_s":%s}' \
            "$(_json_escape "${TEST_NAMES[$i]}")" \
            "${TEST_STATES[$i]}" \
            "${TEST_DURATIONS[$i]}")
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

    printf '%s\n' "${tests[@]}"
}

# ── Results HTTP server ──────────────────────────────────────────────

RESULTS_SERVER_PID=""

start_results_server() {
    # HTTP server with two endpoints:
    #   GET /test-status  — JSON snapshot (for simple polling)
    #   GET /test-stream  — SSE stream (pushes full state on every change)
    #
    # The SSE stream means the client always has results up to the moment
    # the instance dies — no data loss from polling gaps.
    python3 -c "
import http.server, json, os, sys, time, threading

RESULTS = '${RESULTS_FILE}'
POLL_INTERVAL = 0.5

def read_results():
    try:
        with open(RESULTS) as f:
            return f.read().strip()
    except FileNotFoundError:
        return json.dumps({'state': 'pending'})

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test-status':
            self._serve_json()
        elif self.path == '/test-stream':
            self._serve_sse()
        else:
            self.send_error(404)

    def _serve_json(self):
        body = read_results()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body.encode())

    def _serve_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

        last_content = None
        try:
            while True:
                content = read_results()
                if content != last_content:
                    last_content = content
                    self.wfile.write(f'data: {content}\n\n'.encode())
                    self.wfile.flush()
                    # Stop streaming once tests reach a terminal state
                    try:
                        state = json.loads(content).get('state')
                        if state in ('passed', 'failed'):
                            break
                    except (json.JSONDecodeError, AttributeError):
                        pass
                time.sleep(POLL_INTERVAL)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass

server = http.server.HTTPServer(('0.0.0.0', ${RESULTS_PORT}), Handler)
server.daemon_threads = True
server.serve_forever()
" &
    RESULTS_SERVER_PID=$!
    echo "Results server started on port ${RESULTS_PORT} (pid ${RESULTS_SERVER_PID})"
}

stop_results_server() {
    if [[ -n "${RESULTS_SERVER_PID:-}" ]]; then
        kill "$RESULTS_SERVER_PID" 2>/dev/null || true
        wait "$RESULTS_SERVER_PID" 2>/dev/null || true
    fi
}
trap stop_results_server EXIT

# ── Post-test actions (automated mode only) ──────────────────────────

post_test_webhook() {
    if [[ -n "${INSTANCE_TEST_WEBHOOK:-}" ]]; then
        curl -sf -X POST -H "Content-Type: application/json" \
            -d @"${RESULTS_FILE}" "${INSTANCE_TEST_WEBHOOK}" \
            --max-time 10 || echo "Webhook POST failed (non-fatal)"
    fi
}

stop_instance() {
    local container_id="${CONTAINER_ID:-}"
    local api_key="${CONTAINER_API_KEY:-}"
    if [[ -z "$container_id" || -z "$api_key" ]]; then
        echo "Cannot stop instance: CONTAINER_ID or CONTAINER_API_KEY not set"
        return
    fi
    echo "Stopping instance ${container_id}..."
    vastai stop instance "$container_id" --api-key "$api_key" || echo "Stop command failed (non-fatal)"
}

# ── Main ─────────────────────────────────────────────────────────────

declare -a TEST_NAMES=()
declare -a TEST_STATES=()
declare -a TEST_DURATIONS=()

# Start HTTP results server (automated mode only)
if [[ "$MANUAL" == "false" ]]; then
    start_results_server
fi

# Wait for provisioning to finish (automated mode only)
if [[ "$MANUAL" == "false" ]]; then
    echo "Waiting for provisioning to complete..."
    prov_wait=0
    prov_timeout="${INSTANCE_TEST_PROV_TIMEOUT:-600}"
    while [[ -f /.provisioning ]]; do
        sleep 5
        prov_wait=$((prov_wait + 5))
        if [[ $prov_wait -ge $prov_timeout ]]; then
            echo "WARNING: /.provisioning still exists after ${prov_timeout}s — proceeding anyway"
            break
        fi
    done
    echo "Provisioning done, waiting for services to stabilize..."
    sleep 10
fi

# Discover tests
mapfile -t ALL_TESTS < <(discover_tests)

if [[ ${#ALL_TESTS[@]} -eq 0 ]]; then
    echo "No tests found in ${TESTS_DIR}"
    write_results "passed"
    exit 0
fi

echo "Discovered ${#ALL_TESTS[@]} tests"

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

    echo "─── Running: ${test_name} ───"
    TEST_STATES[$i]="running"
    write_results "running"

    test_start=$(date +%s)
    set +e
    # Run test with its name exported, timeout after 120s
    TEST_NAME="$test_name" timeout 120 bash "$test_path" 2>&1
    rc=$?
    set -e
    test_end=$(date +%s)
    TEST_DURATIONS[$i]=$((test_end - test_start))

    case $rc in
        0)
            TEST_STATES[$i]="passed"
            echo "  → PASSED (${TEST_DURATIONS[$i]}s)"
            ;;
        77)
            TEST_STATES[$i]="skipped"
            echo "  → SKIPPED (${TEST_DURATIONS[$i]}s)"
            ;;
        124)
            TEST_STATES[$i]="failed"
            has_failure=true
            echo "  → FAILED (timeout after 120s)"
            ;;
        *)
            TEST_STATES[$i]="failed"
            has_failure=true
            echo "  → FAILED (exit code ${rc}, ${TEST_DURATIONS[$i]}s)"
            ;;
    esac

    write_results "running"
done

# Final state
if $has_failure; then
    write_results "failed"
    echo "══════════════════════════════"
    echo "  TEST SUITE FAILED"
    echo "══════════════════════════════"
else
    write_results "passed"
    echo "══════════════════════════════"
    echo "  ALL TESTS PASSED"
    echo "══════════════════════════════"
fi

# Post-test actions (automated mode only)
if [[ "$MANUAL" == "false" ]]; then
    post_test_webhook
    # Give SSE clients time to receive final event
    sleep 2
    stop_instance
fi

$has_failure && exit 1 || exit 0
