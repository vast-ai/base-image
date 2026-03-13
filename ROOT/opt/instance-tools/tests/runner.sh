#!/bin/bash
# Instance test runner — discovers and executes test scripts, writes JSON results.
#
# Test discovery order:
#   1. base/*.sh    — base image tests (always present)
#   2. *.d/*.sh     — derivative image tests (dropped by derivative images)
#
# Each test script must exit: 0 (pass), 1 (fail), 2 (fatal — aborts suite), 77 (skip).
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
OUTPUT_LOG="${INSTANCE_TEST_LOG:-/var/log/test-output.log}"
DEFAULT_TEST_TIMEOUT="${INSTANCE_TEST_DEFAULT_TIMEOUT:-3600}"  # 1 hour; override per-test with # TEST_TIMEOUT=N
START_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
START_EPOCH=$(date +%s)

# ── Manual mode detection ────────────────────────────────────────────

# Auto-detect manual mode: if run from a TTY (interactive shell), default to manual.
# The boot script backgrounds us (no TTY), so automated mode only activates that way.
# --manual / --auto flags override the auto-detection.
if [[ "${1:-}" == "--manual" ]]; then
    MANUAL=true
elif [[ "${1:-}" == "--auto" ]]; then
    MANUAL=false
elif [[ -t 0 || -t 1 ]]; then
    # stdin or stdout is a terminal — someone ran this interactively
    MANUAL=true
else
    MANUAL=false
fi

if [[ "$MANUAL" == "true" ]]; then
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

# ── Output logging ───────────────────────────────────────────────────
# In automated mode, all output goes to both stdout and a log file.
# The SSE server streams the log file line-by-line to connected clients.

: > "$OUTPUT_LOG"  # truncate

# log_output: write to both stdout and the log file
log_output() {
    while IFS= read -r line; do
        printf '%s\n' "$line"
        printf '%s\n' "$line" >> "$OUTPUT_LOG"
    done
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
CLIENT_READY_FILE="/tmp/.test-client-connected"

start_results_server() {
    # HTTP server with endpoints:
    #   GET /test-status      — JSON snapshot (for simple polling)
    #   GET /test-stream      — SSE stream of raw test output lines + final JSON state
    #   GET /test-stream?log=1 — also interleave system logs (comma-separated in INSTANCE_TEST_SYSTEM_LOG)
    #   POST /test-start      — Signal that client is connected, tests can begin
    #
    # The SSE stream tails the output log, sending each line as it appears.
    # With ?log=1, it also tails the system log file (ANSI passthrough).
    # When tests finish, it sends a final "result" event with the JSON summary.
    rm -f "${CLIENT_READY_FILE}"
    python3 -c "
import http.server, json, os, sys, time, threading, io
from urllib.parse import urlparse, parse_qs

RESULTS = '${RESULTS_FILE}'
OUTPUT_LOG = '${OUTPUT_LOG}'
SYSTEM_LOGS = [p.strip() for p in '${INSTANCE_TEST_SYSTEM_LOG:-}'.split(',') if p.strip()]
READY_FILE = '${CLIENT_READY_FILE}'

def read_results():
    try:
        with open(RESULTS) as f:
            return f.read().strip()
    except FileNotFoundError:
        return json.dumps({'state': 'pending'})

def signal_client_ready():
    if not os.path.exists(READY_FILE):
        open(READY_FILE, 'w').close()

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/test-status':
            self._serve_json()
        elif parsed.path == '/test-stream':
            signal_client_ready()
            qs = parse_qs(parsed.query)
            include_log = qs.get('log', ['0'])[0] == '1'
            self._serve_sse(include_log)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/test-start':
            signal_client_ready()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'{\"ok\":true}')
        else:
            self.send_error(404)

    def _serve_json(self):
        body = read_results()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body.encode())

    def _send_sse(self, event, data):
        self.wfile.write(f'event: {event}\ndata: {data}\n\n'.encode())
        self.wfile.flush()

    def _serve_sse(self, include_log=False):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

        # newline='\\n': disable universal newlines so \\r is NOT
        # treated as a line ending.  Progress bars use \\r to
        # overwrite in place — we need it in the line content.
        output_f = open(OUTPUT_LOG, 'r', newline='\\n')
        log_files = {}   # path -> file handle
        log_buffers = {} # path -> partial line buffer
        last_heartbeat = time.time()
        HEARTBEAT_INTERVAL = 5  # seconds between keep-alive comments

        def _resolve_cr(raw):
            '''Strip line endings, resolve mid-line \\r to last segment.
            Returns (clean_line, had_cr).'''
            s = raw.rstrip('\\r\\n')
            if '\\r' in s:
                return s.rsplit('\\r', 1)[-1], True
            return s, False

        try:
            while True:
                had_data = False

                # Read test output lines (complete lines only — log_output
                # writes with printf '%s\\n' so readline always gets \\n)
                line = output_f.readline()
                if line:
                    had_data = True
                    clean, _ = _resolve_cr(line)
                    self._send_sse('output', json.dumps(clean))

                # Read system log lines (multiple files supported).
                # Tailed live — readline() may return partial lines (no
                # trailing \\n).  We buffer per-file and emit two ways:
                #   - Complete line (has \\n): emit normally, clear buffer.
                #     If line had \\r, resolve to last segment + overwrite flag.
                #   - Partial line (no \\n): emit as overwrite preview so the
                #     client shows live updates (progress bars, status lines).
                #     The next chunk extends the buffer until \\n commits it.
                if include_log:
                    for log_path in SYSTEM_LOGS:
                        if log_path not in log_files:
                            if os.path.exists(log_path):
                                f = open(log_path, 'r', newline='\\n')
                                log_files[log_path] = f
                                log_buffers[log_path] = ''
                        if log_path in log_files:
                            chunk = log_files[log_path].readline()
                            if chunk:
                                had_data = True
                                log_buffers[log_path] += chunk
                                buf = log_buffers[log_path]
                                src = os.path.basename(log_path)
                                if buf.endswith('\\n'):
                                    # Complete line — commit and clear buffer
                                    log_buffers[log_path] = ''
                                    log_clean, had_cr = _resolve_cr(buf)
                                    msg = {'src': src, 'line': log_clean}
                                    if had_cr:
                                        msg['overwrite'] = True
                                    self._send_sse('log', json.dumps(msg))
                                else:
                                    # Partial line — send live preview as overwrite
                                    log_clean, _ = _resolve_cr(buf)
                                    msg = {'src': src, 'line': log_clean, 'overwrite': True}
                                    self._send_sse('log', json.dumps(msg))

                if not had_data:
                    # Check if tests finished
                    results = read_results()
                    try:
                        state = json.loads(results).get('state')
                        if state in ('passed', 'failed'):
                            self._send_sse('result', results)
                            break
                    except (json.JSONDecodeError, AttributeError):
                        pass
                    # Send SSE heartbeat comment to keep connection alive.
                    # Tests like vLLM health can poll silently for minutes.
                    now = time.time()
                    if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                        self.wfile.write(b': heartbeat\n\n')
                        self.wfile.flush()
                        last_heartbeat = now
                    time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            output_f.close()
            for f in log_files.values():
                f.close()

    def log_message(self, *a):
        pass

port = int('${RESULTS_PORT}') if '${RESULTS_PORT}'.isdigit() else 10199
# ThreadingHTTPServer so /test-status can be served while /test-stream
# is active (SSE blocks the handler thread for the stream's lifetime).
if hasattr(http.server, 'ThreadingHTTPServer'):
    server = http.server.ThreadingHTTPServer(('0.0.0.0', port), Handler)
else:
    # Python 3.6 fallback
    import socketserver
    class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
    server = ThreadedServer(('0.0.0.0', port), Handler)
server.daemon_threads = True
server.serve_forever()
" &
    RESULTS_SERVER_PID=$!
    echo "Results server started on port ${RESULTS_PORT} (pid ${RESULTS_SERVER_PID})"
}

wait_for_client() {
    # Wait up to 2 hours for a client to connect before starting tests.
    # This prevents tests from running and completing before anyone is watching.
    local timeout=7200
    local elapsed=0
    echo "Waiting for test client to connect..."
    while [[ ! -f "${CLIENT_READY_FILE}" ]] && (( elapsed < timeout )); do
        sleep 1
        elapsed=$((elapsed + 1))
    done
    if [[ -f "${CLIENT_READY_FILE}" ]]; then
        echo "Client connected, starting tests"
    else
        echo "No client connected after ${timeout}s, starting tests anyway"
    fi
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

# ── Main ─────────────────────────────────────────────────────────────

declare -a TEST_NAMES=()
declare -a TEST_STATES=()
declare -a TEST_DURATIONS=()

# Start HTTP results server (automated mode only)
if [[ "$MANUAL" == "false" ]]; then
    start_results_server
fi

# In automated mode, wait for a client to connect before running tests.
# This ensures the SSE stream is established and no results are missed.
if [[ "$MANUAL" == "false" ]]; then
    wait_for_client
fi

# No blind provisioning wait — test 12-provisioning.sh handles monitoring.
# Tests before 12 run immediately; tests after 12 run once provisioning is confirmed.

# Discover tests
mapfile -t ALL_TESTS < <(discover_tests)

if [[ ${#ALL_TESTS[@]} -eq 0 ]]; then
    echo "No tests found in ${TESTS_DIR}"
    write_results "passed"
    exit 0
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

    echo "─── Running: ${test_name} ───" | log_output
    TEST_STATES[$i]="running"
    write_results "running"

    test_start=$(date +%s)
    # Per-test timeout: check for TEST_TIMEOUT in script header, fall back to DEFAULT_TEST_TIMEOUT.
    test_timeout=$(grep -oP '^# TEST_TIMEOUT=\K\d+' "$test_path" 2>/dev/null || echo "$DEFAULT_TEST_TIMEOUT")
    set +e
    # Run test with clean shell options — don't leak errexit/nounset via SHELLOPTS.
    TEST_NAME="$test_name" timeout "$test_timeout" env -u SHELLOPTS bash "$test_path" 2>&1 | log_output
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
            echo "  → FAILED (timeout after ${test_timeout}s)" | log_output
            ;;
        *)
            TEST_STATES[$i]="failed"
            has_failure=true
            echo "  → FAILED (exit code ${rc}, ${TEST_DURATIONS[$i]}s)" | log_output
            ;;
    esac

    write_results "running"
done

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

# Post-test actions (automated mode only)
if [[ "$MANUAL" == "false" ]]; then
    post_test_webhook
    # Keep HTTP server alive so clients can poll /test-status for final results.
    # The SSE result event may race with our exit; this ensures it's fetchable.
    sleep 30
fi

$has_failure && exit 1 || exit 0
