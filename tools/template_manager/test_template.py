#!/usr/bin/env python3
"""Launch a Vast.ai instance from a template and stream test results."""

import argparse
import errno
import json
import os
import signal
import sys
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://console.vast.ai"

# Port the in-instance test results server binds on.  Must match the value
# baked into the vastai/* base images' runner script.
TEST_SERVER_PORT = 10199

# ── Timeouts (seconds) ───────────────────────────────────────────────────
DEFAULT_TEST_TIMEOUT = 7200      # 2 hours — total time for test suite to complete
POLL_TIMEOUT = 7200              # 2 hours — max wait for instance to reach "running"
API_REQUEST_TIMEOUT = 30         # individual API call timeout
SSE_READ_TIMEOUT = 30            # read timeout per attempt (server heartbeats every 5s)
TIMEOUT_HEADROOM = 600           # 10 min headroom added to computed minimum timeout

# Instance-side timeout env vars and their defaults.  Used to auto-compute
# the minimum sensible client --timeout so we don't time out before the
# instance does.  These are sequential phases: provisioning runs first,
# then all tests (including derivative tests like vLLM health).
INSTANCE_TIMEOUT_ENVS = {
    "PROV_TIMEOUT": 3600,                    # 12-provisioning.sh total wait
    "VLLM_HEALTH_TIMEOUT": 3600,             # vLLM model loading + graph compilation
    "INSTANCE_TEST_DEFAULT_TIMEOUT": 3600,   # runner.sh per-test fallback
}

# ── Verdict: exit codes + machine-readable outcome (ADR 0005) ────────────
# Exit codes are distinct so CI can act on $?, and --raw emits a JSON line with
# a "state" field on EVERY terminal path so CI reads the verdict from the
# payload, not just the code. The two never disagree.
EXIT_PASSED = 0           # state "passed"   — all tests passed
EXIT_FAILED = 1           # state "failed"   — a test failed (block promotion)
EXIT_NO_OFFERS = 2        # state "no_offers"     — thin market (inconclusive)
EXIT_BAD_INSTANCE = 3     # state "bad_instance"  — exhausted launch attempts (infra)
EXIT_CONFIG_ERROR = 4     # state "config_error"  — template/arg/image/auth/API error (CI bug)
EXIT_INSTANCE_ERROR = 5   # state "instance_error"— instance crashed mid-test (treat as the image)
EXIT_INTERRUPTED = 130    # state "interrupted"   — SIGINT/SIGTERM
BAD_INSTANCE_EXIT_CODE = EXIT_BAD_INSTANCE  # back-compat alias

_RAW_MODE = False  # set from args.raw in main(); read by emit_outcome()


def emit_outcome(state, code, **extra):
    """Emit a machine-readable terminal outcome and exit.

    When --raw is set, prints one JSON line to stdout (``{"state", "exit_code",
    ...}``) so CI reads the verdict from the payload. Always exits with ``code``.
    Used for the pre-test terminal paths; the post-test path emits the richer
    result JSON inline and exits with the mapped code.
    """
    if _RAW_MODE:
        print(json.dumps({"state": state, "exit_code": code, **extra}))
    sys.exit(code)


def classify_outcome(final_state):
    """Map the runner's final_state to a (machine-readable state, exit code).

    "passed"/"failed" are authoritative; anything else ("error", or an
    unexpected value) means the instance did not finish cleanly and is treated
    as the image's problem (instance_error), not a soft "inconclusive".
    """
    if final_state == "passed":
        return "passed", EXIT_PASSED
    if final_state == "failed":
        return "failed", EXIT_FAILED
    return "instance_error", EXIT_INSTANCE_ERROR


# ── Retry + disk verification tuning ─────────────────────────────────────
MAX_LAUNCH_ATTEMPTS = 25       # how many offers to try before giving up
DISK_TOLERANCE = 1.0           # instance must provision at least the full requested disk
OFFER_CANDIDATE_POOL = 50      # cap on offers kept for the retry loop
VRAM_CEILING_MULTIPLIER = 3.0  # bound the VRAM search at N x the declared floor
                               # (ADR 0005: don't test a >=8GB claim on a 96GB box).
                               # 3x = an 8GB floor admits the abundant 24GB consumer
                               # tier (RTX 3090/4090) to widen a thin market, while
                               # still excluding the 40/80GB datacenter cards. This
                               # band is the cost control now the price filter is gone.
NETWORK_PROBE_TIMEOUT = 60     # seconds of nothing-but-connection-timeouts before an
                               # instance is judged to have broken host networking.
                               # Connection *refused* does NOT count against this window —
                               # it proves the host is reachable (port forwarding works),
                               # the test server simply has not bound the port yet.  Once
                               # any refused/HTTP reply is seen, probe_test_server switches
                               # to the longer provisioning-aware deadline: the boot
                               # sequence runs provisioning to completion before it starts
                               # the test runner, so the server can be many minutes away.


# ── Terminal split panel for log output ──────────────────────────────────

class LogPanel:
    """Split terminal: test output scrolls in top region, logs in fixed bottom panel.

    Uses ANSI scroll regions so the top area scrolls naturally while the bottom
    panel is redrawn in place. Falls back to inline dimmed output when stderr
    is not a TTY or the terminal is too small.
    """

    def __init__(self, enabled=True, panel_height=12):
        self.active = False
        self._log_lines = []              # panel lines (list for indexed overwrite)
        self._max_log_lines = 200         # cap to prevent unbounded growth
        self._panel_height = panel_height
        self._rows = 0
        self._cols = 80
        self._last_output_was_cr = False  # track \r lines for in-place overwrite
        # Per-file progress state: maps src → index in _log_lines of the
        # "active" line that should be overwritten on rapid updates.
        # Cleared when a different src writes, committing the progress line.
        self._file_progress = {}          # {src: int}
        if not enabled or not sys.stderr.isatty():
            return
        try:
            size = os.get_terminal_size(sys.stderr.fileno())
            self._rows = size.lines
            self._cols = size.columns
        except (ValueError, OSError):
            return
        if self._rows < 24:
            return  # too small to split
        self._panel_height = min(panel_height, self._rows // 3)
        self._scroll_end = self._rows - self._panel_height - 1  # 1 for separator
        self.active = True
        # Set scroll region for test output (top portion)
        self._esc(f"\033[1;{self._scroll_end}r")
        self._esc(f"\033[{self._scroll_end};1H")
        self._draw_separator()

    def _esc(self, seq):
        sys.stderr.write(seq)

    def _draw_separator(self, label="logs"):
        self._esc("\033[s")  # save cursor
        sep = f" ── {label} "
        sep = sep + "─" * max(0, self._cols - len(sep))
        self._esc(f"\033[{self._scroll_end + 1};1H\033[2m{sep[:self._cols]}\033[0m")
        self._esc("\033[u")  # restore cursor
        sys.stderr.flush()

    def write_output(self, line):
        r"""Write a test output line (top scroll region).

        Handles progress bars that use \r to overwrite in place.  A line
        like "50%\r75%\r100%" has mid-line \r — we show only the last
        segment and stay on the current terminal line.  Trailing \r (from
        \r\n line endings) is stripped and treated as a normal line.
        """
        # Strip trailing \r (CRLF artifact), then check for mid-line \r
        line = line.rstrip("\r")
        is_cr = "\r" in line
        if self.active:
            if is_cr:
                segment = line.rsplit("\r", 1)[-1]
                self._esc(f"\r\033[K{segment}")
            else:
                if self._last_output_was_cr:
                    self._esc("\n")
                self._esc(f"{line}\n")
            sys.stderr.flush()
        else:
            if is_cr:
                segment = line.rsplit("\r", 1)[-1]
                print(f"\r\033[K{segment}", end="", file=sys.stderr, flush=True)
            else:
                if self._last_output_was_cr:
                    print(file=sys.stderr)
                print(line, file=sys.stderr)
        self._last_output_was_cr = is_cr

    def write_log(self, line, src="", overwrite=False):
        r"""Write a log line (bottom panel or inline dimmed).

        Per-file progress tracking: when the server signals overwrite=True
        (line contained \r — a progress bar), the previous line from the
        same source is replaced in-place.  Multiple files can each have
        their own independent active progress line.  Normal lines (no
        overwrite flag) always append and clear the source's active slot.
        """
        line = line.rstrip("\r")
        if "\r" in line:
            line = line.rsplit("\r", 1)[-1]
        prefix = f"\033[36m{src}\033[2m " if src else ""
        formatted = f"{prefix}{line}"
        if self.active:
            if overwrite and src:
                # Progress line — overwrite this source's active slot, or append
                idx = self._file_progress.get(src)
                if idx is not None and 0 <= idx < len(self._log_lines):
                    self._log_lines[idx] = formatted
                else:
                    self._log_lines.append(formatted)
                    self._file_progress[src] = len(self._log_lines) - 1
            else:
                # Normal line — append and clear any active progress slot
                self._log_lines.append(formatted)
                self._file_progress.pop(src, None)
            # Trim old lines to prevent unbounded growth
            if len(self._log_lines) > self._max_log_lines:
                trim = len(self._log_lines) - self._max_log_lines
                self._log_lines = self._log_lines[trim:]
                stale = []
                for s, idx in self._file_progress.items():
                    idx -= trim
                    if idx < 0:
                        stale.append(s)
                    else:
                        self._file_progress[s] = idx
                for s in stale:
                    del self._file_progress[s]
            self._refresh_panel()
        else:
            if overwrite:
                print(f"\r\033[K\033[2m{formatted}\033[0m",
                      end="", file=sys.stderr, flush=True)
            else:
                print(f"\033[2m{formatted}\033[0m", file=sys.stderr)

    def _refresh_panel(self):
        self._esc("\033[s")  # save cursor
        # Draw the most recent lines that fit in the panel
        visible = self._log_lines[-self._panel_height:]
        for i in range(self._panel_height):
            row = self._scroll_end + 2 + i
            self._esc(f"\033[{row};1H\033[K")
            if i < len(visible):
                # Truncate to terminal width, preserve ANSI
                self._esc(f"\033[2m{visible[i]}\033[0m")
        self._esc("\033[u")  # restore cursor
        sys.stderr.flush()

    def cleanup(self):
        """Reset terminal to normal state."""
        if self.active:
            self._esc("\033[r")  # reset scroll region
            self._esc(f"\033[{self._rows};1H\n")  # move to bottom
            sys.stderr.flush()
            self.active = False


class VastAPI:
    """Thin wrapper around Vast REST API."""

    def __init__(self, api_key):
        self.api_key = api_key

    def _do_request(self, method, path, body=None, query=None):
        """Execute the HTTP request. Raises HTTPError/URLError on failure."""
        url = API_BASE + path
        if query:
            params = {k: v if isinstance(v, str) else json.dumps(v) for k, v in query.items()}
            url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote_plus)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=API_REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode()
            if not raw:
                return {}
            return json.loads(raw)

    def _request(self, method, path, body=None, query=None):
        """Make authenticated API request, return parsed JSON.  Exits on
        transport/HTTP errors — used for one-shot calls where retry is not
        meaningful.  Long-running pollers should use ``_request_safe``."""
        try:
            return self._do_request(method, path, body, query)
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode()
            except Exception:
                pass
            print(f"API error {e.code} {method} {path}: {body_text}", file=sys.stderr)
            emit_outcome("config_error", EXIT_CONFIG_ERROR, reason=f"API error {e.code} on {path}")
        except urllib.error.URLError as e:
            print(f"API connection error {method} {path}: {e.reason}", file=sys.stderr)
            emit_outcome("config_error", EXIT_CONFIG_ERROR, reason=f"API connection error on {path}")

    def _request_safe(self, method, path, body=None, query=None):
        """Like ``_request`` but returns ``None`` on transport/HTTP errors
        instead of exiting.  Used by pollers and the health watchdog so a
        single transient API blip during a multi-hour run is not fatal."""
        try:
            return self._do_request(method, path, body, query)
        except (urllib.error.HTTPError, urllib.error.URLError,
                TimeoutError, OSError):
            return None

    def get_template(self, hash_id):
        """Fetch template by hash_id via search endpoint."""
        result = self._request("GET", "/api/v0/template/", query={
            "select_cols": ["*"],
            "select_filters": {"hash_id": {"eq": hash_id}},
        })
        templates = result.get("templates", [])
        if not templates:
            print(f"Template '{hash_id}' not found", file=sys.stderr)
            emit_outcome("config_error", EXIT_CONFIG_ERROR, reason="template not found")
        return templates[0]

    def search_offers(self, filters):
        return self._request("POST", "/api/v0/bundles/", filters)

    def create_instance(self, offer_id, payload):
        # Return a failure dict instead of exiting so the retry loop can
        # survive a per-offer HTTP error (e.g. 409 "offer no longer available").
        try:
            return self._request("PUT", f"/api/v0/asks/{offer_id}/", payload)
        except SystemExit:
            return {"success": False, "msg": "API error (see stderr)"}

    def get_instance(self, instance_id, safe=False):
        """Fetch instance via list endpoint (has Docker-style port mappings).

        Returns ``{}`` if the instance is not in the user's list (e.g.
        destroyed externally).  When ``safe=True``, transport/HTTP errors
        return ``None`` instead of exiting so callers in retry loops can
        distinguish a destroyed instance from a transient API blip.
        """
        request = self._request_safe if safe else self._request
        result = request("GET", "/api/v0/instances/", query={"owner": "me"})
        if result is None:
            return None
        for inst in result.get("instances", []):
            if inst.get("id") == instance_id:
                return inst
        return {}

    def get_instance_status(self, instance_id, safe=False):
        """Fetch a single instance via ``/api/v0/instances/{id}/`` — O(1).

        Suitable for the health watchdog which only needs ``actual_status``.
        The single-instance endpoint does not include Docker-style port
        mappings, so the startup poll still uses ``get_instance``.

        Returns ``{}`` if the instance is missing, ``None`` on transport
        errors when ``safe=True``.  The endpoint shape is
        ``{"instances": [{...}]}`` for a live instance and
        ``{"instances": null}`` for an unknown id.
        """
        request = self._request_safe if safe else self._request
        result = request("GET", f"/api/v0/instances/{instance_id}/")
        if result is None:
            return None
        insts = result.get("instances")
        if isinstance(insts, list) and insts:
            return insts[0]
        if isinstance(insts, dict):
            return insts
        return {}

    @staticmethod
    def get_status(test_url, token=None):
        """Fetch final test status from instance's /test-status endpoint."""
        req = urllib.request.Request(test_url + "/test-status")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=API_REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode())

    def stop_instance(self, instance_id):
        # _request_safe: an instance that's already gone (404) makes teardown a
        # no-op rather than a hard exit — a mid-test spot reclaim must not turn
        # cleanup into a spurious config_error.
        return self._request_safe("PUT", f"/api/v0/instances/{instance_id}/",
                                  {"state": "stopped"})

    def destroy_instance(self, instance_id):
        return self._request_safe("DELETE", f"/api/v0/instances/{instance_id}/")


def format_filters(filters):
    """Format filter dict for human-readable display."""
    ops = {"eq": "=", "gte": ">=", "lte": "<=", "gt": ">", "lt": "<", "in": "in"}
    parts = []
    skip = {"verified", "external", "rentable", "rented"}
    for key, val in filters.items():
        if key in skip:
            continue
        if isinstance(val, dict):
            for op, v in val.items():
                parts.append(f"{key} {ops.get(op, op)} {v}")
        else:
            parts.append(f"{key} = {val}")
    return ", ".join(parts) if parts else "(base filters only)"


TERMINAL_STATES = {"stopped", "offline", "error", "exited"}

# Status-message substrings that indicate the instance is unrecoverable but
# may never transition to a TERMINAL_STATES value — polling would otherwise
# wait until timeout.  Matched case-insensitively against ``status_msg``.
BAD_STATUS_PATTERNS = (
    "error response from daemon",      # Docker daemon errors (image pull, etc.)
    "failed to pull image",
    "no space left on device",
    "manifest unknown",
    "unauthorized",
)


def _bad_status_reason(status_msg):
    if not status_msg:
        return None
    lower = status_msg.lower()
    for pat in BAD_STATUS_PATTERNS:
        if pat in lower:
            return pat
    return None


def poll_until_running(api, instance_id, timeout=POLL_TIMEOUT):
    """Poll instance until running with ports available.

    Returns (test_url, auth_token) tuple. auth_token is the instance's
    jupyter_token used as Bearer auth for the test server, or None if
    the instance doesn't provide one.  Returns (None, None) on failure.
    """
    deadline = time.time() + timeout
    last_status = ""
    while time.time() < deadline:
        inst = api.get_instance(instance_id, safe=True)

        # Transient API error — sleep and retry rather than crash the whole
        # tool on a single network blip during a multi-hour boot poll.
        if inst is None:
            time.sleep(5)
            continue

        if not inst:
            print(f"\r\033[KInstance {instance_id} not found (destroyed externally?)",
                  file=sys.stderr)
            return None, None

        status = inst.get("actual_status", "")
        status_msg = inst.get("status_msg", "")

        # Update status line in-place when it changes
        current = status_msg.strip() if status_msg else status
        if current and current != last_status:
            last_status = current
            # Clear line and overwrite
            print(f"\r\033[K  Status: {current}", end="", flush=True, file=sys.stderr)

        # Detect terminal failure states during startup
        if status in TERMINAL_STATES:
            print(f"\r\033[KInstance {instance_id} entered unexpected state: {status}",
                  file=sys.stderr)
            return None, None

        # Detect unrecoverable status messages (e.g. "Error response from
        # daemon") that never transition to a terminal state — no point
        # waiting out the full POLL_TIMEOUT.
        bad = _bad_status_reason(status_msg)
        if bad:
            print(f"\r\033[KInstance {instance_id} has bad status: {status_msg.strip()}",
                  file=sys.stderr)
            return None, None

        if status == "running" and inst.get("public_ipaddr"):
            ports = inst.get("ports", {})
            test_port = None
            for key, mappings in (ports or {}).items():
                if key.startswith(str(TEST_SERVER_PORT)):
                    if isinstance(mappings, list) and mappings:
                        test_port = mappings[0].get("HostPort")
                    break

            if test_port:
                host = inst["public_ipaddr"]
                token = inst.get("jupyter_token")
                # Clear status line and print final
                print(f"\r\033[KInstance {instance_id} running ({host}:{test_port})",
                      file=sys.stderr)
                return f"http://{host}:{test_port}", token

        time.sleep(5)

    print(f"\r\033[KTimeout waiting for instance {instance_id} to start", file=sys.stderr)
    return None, None


def _parse_sse_event(data_lines, event_type):
    """Parse SSE data lines into an event dict."""
    raw_data = "\n".join(data_lines)
    try:
        parsed = json.loads(raw_data)
    except json.JSONDecodeError:
        parsed = raw_data
    if isinstance(parsed, dict):
        parsed["_event"] = event_type
    else:
        parsed = {"_event": event_type, "_data": parsed}
    return parsed


def stream_sse(url, timeout, token=None):
    """Connect to SSE endpoint, yield parsed events.

    Two phases:
      1. Connection phase — retries every 5s for up to 5 min while the instance
         boots and the test server starts.  Uses SSE_READ_TIMEOUT per attempt.
      2. Streaming phase — once connected, uses SSE_READ_TIMEOUT as the socket
         read timeout.  If no data arrives within that window, the connection is
         considered lost and we exit immediately (no reconnect).  A dead instance
         won't recover, so there's no point retrying.
    """
    deadline = time.time() + timeout
    retries = 0
    max_retries = 60  # 60 * 5s = 5 min for boot scripts + test server startup
    connected = False

    while time.time() < deadline and retries <= max_retries:
        try:
            req = urllib.request.Request(url)
            req.add_header("Accept", "text/event-stream")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            resp = urllib.request.urlopen(
                req, timeout=min(SSE_READ_TIMEOUT, remaining))

            # ── Connected ─────────────────────────────────────────────
            if retries > 0:
                print(" connected", flush=True, file=sys.stderr)
            connected = True

            # Set socket-level read timeout so readline() doesn't hang
            # indefinitely if the instance dies mid-stream.
            try:
                sock = resp.fp.raw
                if hasattr(sock, '_sock'):
                    sock._sock.settimeout(SSE_READ_TIMEOUT)
                elif hasattr(sock, 'settimeout'):
                    sock.settimeout(SSE_READ_TIMEOUT)
            except (AttributeError, OSError):
                pass  # fall back to urlopen timeout

            event_type = None
            data_lines = []

            while True:
                raw_line = resp.readline()
                if not raw_line:
                    break
                if time.time() > deadline:
                    yield {"_timeout": True}
                    return

                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line == "" and data_lines:
                    yield _parse_sse_event(data_lines, event_type)
                    event_type = None
                    data_lines = []

            # Stream ended cleanly — flush any remaining data
            if data_lines and event_type is not None:
                yield _parse_sse_event(data_lines, event_type)
            return

        except (urllib.error.URLError, OSError, TimeoutError):
            if connected:
                # Lost connection after we were streaming — instance is gone
                yield {"_error": f"connection lost (no data/heartbeat for {SSE_READ_TIMEOUT}s)"}
                return
            retries += 1
            if retries > max_retries:
                print("Could not connect to test server", file=sys.stderr)
                yield {"_error": "connection failed after retries"}
                return
            if retries == 1:
                print("Waiting for test server...", end="", flush=True, file=sys.stderr)
            else:
                print(".", end="", flush=True, file=sys.stderr)
            time.sleep(5)

    yield {"_timeout": True}


def format_summary(state, counts, elapsed=None, failed_names=None):
    """Format the final summary line."""
    color = "\033[32m" if state == "passed" else "\033[31m"
    reset = "\033[0m"
    dur_str = f" ({elapsed}s)" if elapsed is not None else ""
    summary = (f"{color}{state.upper()}{reset} — "
               f"{counts['passed']} passed, {counts['failed']} failed, "
               f"{counts['skipped']} skipped{dur_str}")
    if failed_names:
        summary += f"\n  Failed: {', '.join(failed_names)}"
    return summary


def _required_floor(extra_filters, key):
    """Return the minimum value the template asks for (e.g. MB of gpu_total_ram).

    Handles the filter shapes seen in practice: ``{"key": {"gte": N}}``,
    ``{"key": {"gt": N}}``, ``{"key": {"eq": N}}`` and the bare scalar
    ``{"key": N}``.  Returns None if absent or unparseable.
    """
    spec = extra_filters.get(key)
    if spec is None:
        return None
    if isinstance(spec, dict):
        for op in ("gte", "gt", "eq"):
            if op in spec:
                try:
                    return float(spec[op])
                except (TypeError, ValueError):
                    return None
        return None
    try:
        return float(spec)
    except (TypeError, ValueError):
        return None


def _coerce_extra_filters(raw):
    """Coerce a template's ``extra_filters`` (JSON string or dict) to a dict.

    ``extra_filters`` is template-controlled input that reaches us from the Vast
    API, so a malformed publish can make it a non-JSON string or a non-object.
    Raise ``ValueError``/``JSONDecodeError`` here so the caller can map it to a
    clean ``config_error`` verdict instead of crashing with a raw traceback.
    """
    if isinstance(raw, str):
        parsed = json.loads(raw) if raw else {}
    else:
        parsed = raw or {}
    if not isinstance(parsed, dict):
        raise ValueError(
            f"extra_filters must be an object, got {type(parsed).__name__}")
    return parsed


def _base_offer_score(o):
    """Legacy inet/price score; used as the final tie-breaker in sort key."""
    inet = o.get("inet_down", 0) + o.get("inet_up", 0)
    price = max(o.get("dph_total", 1), 0.01)
    dl_cost = o.get("inet_down_cost", 0)  # $/GB
    # Value = internet speed per dollar, penalized by download cost.
    # The 5000 multiplier means $0.01/GB download cost halves the score
    # (1 + 0.01*5000 = 51 → ~50x penalty).  This heavily penalizes
    # metered-bandwidth machines since image pulls are 10-50+ GB.
    return (inet / price) / (1 + dl_cost * 5000)


def apply_vram_ceiling(filters, multiplier):
    """Bound the VRAM search above the declared floor (ADR 0005).

    If the template declares a ``gpu_total_ram`` floor (``gte``) but no upper
    bound, cap the search at ``multiplier × floor`` so a "``>=12GB``" claim is not
    tested on a 96GB box. Templates that set their own ``lte`` are respected.
    Returns a filters dict (the ``gpu_total_ram`` spec is copied, not mutated).
    """
    spec = filters.get("gpu_total_ram")
    if isinstance(spec, dict) and "gte" in spec and "lte" not in spec:
        try:
            ceiling = float(spec["gte"]) * multiplier
        except (TypeError, ValueError):
            return filters
        return {**filters, "gpu_total_ram": {**spec, "lte": ceiling}}
    return filters


def make_offer_sort_key(required_total_mb, required_per_gpu_mb, required_compute_cap=None):
    """Build an ascending sort key for offers (ADR 0005: test the smallest viable box).

    VRAM is primary: a pass at the (headroom-adjusted) VRAM floor generalises up to
    bigger boxes, so prefer the least VRAM above the floor. ``compute_cap`` breaks
    ties — among same-VRAM offers, prefer the lowest capability at or above the
    floor (GPUs are backward-compatible, so a pass at the floor generalises up).

    Components (earlier dominates):
      1. gpu_total_ram overshoot — ``(actual - floor) / floor``; ``inf`` when below
         the floor so undersized offers sink. Prefer the smallest VRAM above the
         declared requirement (the search is also hard-bounded above — see
         ``apply_vram_ceiling``).
      2. gpu_ram overshoot — per-GPU VRAM fit, same shape.
      3. compute_cap above the declared floor — ``compute_cap - floor``; ``inf``
         below the floor. ``compute_cap`` is an integer ×100 (700 = sm_70,
         890 = sm_89/FP8, 900 = H100); the floor (from ``extra_filters``) must
         encode the image's feature target.
      4. num_gpus ascending — smallest fanout that satisfies VRAM.
      5. ``-_base_offer_score(o)`` — inet/price score, negated; tie-breaker, and
         the dominant key when no floor is specified.
    """
    def key(o):
        compute_cap = o.get("compute_cap") or 0
        total_ram = o.get("gpu_total_ram") or 0
        per_gpu_ram = o.get("gpu_ram") or 0
        num_gpus = o.get("num_gpus") or 0

        if required_total_mb:
            total_overshoot = ((total_ram - required_total_mb) / required_total_mb
                               if total_ram >= required_total_mb else float("inf"))
        else:
            total_overshoot = 0.0

        if required_per_gpu_mb:
            per_gpu_overshoot = ((per_gpu_ram - required_per_gpu_mb) / required_per_gpu_mb
                                 if per_gpu_ram >= required_per_gpu_mb else float("inf"))
        else:
            per_gpu_overshoot = 0.0

        if required_compute_cap:
            cap_overshoot = ((compute_cap - required_compute_cap)
                             if compute_cap >= required_compute_cap else float("inf"))
        else:
            cap_overshoot = 0.0

        return (total_overshoot, per_gpu_overshoot, cap_overshoot, num_gpus, -_base_offer_score(o))
    return key


def _safe_destroy(api, instance_id, log):
    try:
        api.destroy_instance(instance_id)
    except Exception as e:
        log(f"    (destroy failed: {e})")


def _is_connection_refused(exc):
    """True when a socket error means 'host reachable, nothing listening yet'.

    Connection refused / reset prove packets round-trip to the host — its
    port forwarding works, there is just no server bound to the port.  A
    timeout or unreachable-host error means the opposite: traffic is being
    black-holed.  ``urllib.error.URLError`` wraps the real error in
    ``.reason``; bare ``OSError`` subclasses are inspected directly.
    """
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, (ConnectionRefusedError, ConnectionResetError)):
        return True
    return getattr(reason, "errno", None) in (errno.ECONNREFUSED, errno.ECONNRESET)


def probe_test_server(test_url, auth_token=None,
                      connectivity_timeout=NETWORK_PROBE_TIMEOUT,
                      server_timeout=NETWORK_PROBE_TIMEOUT, log=None):
    """Return True once the instance's test results server answers HTTP.

    Two failure modes are separated by the *kind* of socket error, so a
    slow-but-healthy host is not mistaken for a broken one:

      * Connection refused / reset — the host is reachable and its port
        forwarding works; nothing is bound to the test port yet.  This is
        expected: the boot sequence runs provisioning (model downloads,
        pip installs) to completion *before* it launches the test runner
        (75-provisioning-manifest.sh precedes 85-instance-test.sh), so the
        results server can be many minutes away.  Keep waiting, up to
        ``server_timeout``.

      * Connection timeout / unreachable — packets are being black-holed.
        Some hosts enter "running" with broken networking and never
        recover.  Nothing but timeouts for ``connectivity_timeout``
        seconds → abandon the instance.

    Any HTTP response (even 4xx/5xx) proves the server is up → return True.
    """
    start = time.time()
    host_reachable = False  # flipped once a refused/reset or HTTP reply arrives
    last_report = 0.0
    while True:
        elapsed = time.time() - start
        # Until the host proves reachable we only allow the short
        # connectivity window; after that, the long provisioning deadline.
        deadline = server_timeout if host_reachable else connectivity_timeout
        if elapsed >= deadline:
            return False
        try:
            req = urllib.request.Request(test_url + "/test-status")
            if auth_token:
                req.add_header("Authorization", f"Bearer {auth_token}")
            with urllib.request.urlopen(req, timeout=5):
                return True
        except urllib.error.HTTPError:
            # Any HTTP response proves the server is up, even non-200.
            # Includes 401 from a missing/bad bearer token — auth failures
            # surface later on the SSE stream rather than here.
            return True
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if _is_connection_refused(e) and not host_reachable:
                host_reachable = True
                if log:
                    log("  Host reachable (port forwarding OK) — waiting for "
                        "the test server; provisioning runs first and may "
                        "take a while...")
            if log and host_reachable and elapsed - last_report >= 60:
                last_report = elapsed
                log(f"  [{elapsed:.0f}s] still waiting for test server "
                    f"(deadline {server_timeout:.0f}s)")
            time.sleep(2)


def monitor_instance_health(api, instance_id, dead_event, set_dead,
                            poll_interval=15):
    """Background watchdog: signal ``set_dead(reason)`` when the instance
    leaves the ``running`` state or is destroyed externally.

    Survives transient API errors so a single network blip during a
    multi-hour run does not silently terminate the thread:

    * ``api.get_instance_status(safe=True)`` returns ``None`` on
      transport/HTTP errors instead of exiting the process.
    * ``BaseException`` catch is belt-and-braces against anything (including
      ``SystemExit``) that might still leak from the call path — ``Exception``
      alone misses ``SystemExit`` because it inherits from ``BaseException``.

    Exits cleanly when ``dead_event`` is set externally (e.g. once the test
    run has finished and the main thread no longer needs the watchdog).
    """
    while not dead_event.is_set():
        dead_event.wait(poll_interval)
        if dead_event.is_set():
            return
        try:
            inst = api.get_instance_status(instance_id, safe=True)
        except BaseException:
            continue
        if inst is None:
            continue  # transient API error — retry next tick
        if not inst:
            set_dead("not found (destroyed externally?)")
            return
        status = inst.get("actual_status", "")
        if status and status != "running":
            set_dead(status)
            return


def launch_with_retry(api, candidate_offers, payload_factory, requested_disk,
                      max_attempts, on_instance_created, log,
                      server_probe_timeout=NETWORK_PROBE_TIMEOUT):
    """Try candidate offers until one launches and provisions adequate disk.

    Walks ``candidate_offers`` (assumed pre-sorted best-first), attempting
    each until one yields a running instance with ``disk_space`` at least
    ``DISK_TOLERANCE * requested_disk``.  Any instance that fails any check
    is destroyed and its offer blacklisted so the same bad machine is not
    retried within the same run.

    ``on_instance_created(instance_id)`` is called the moment an instance_id
    is known, so the outer scope (signal handler) can track the currently
    booting instance for cleanup.

    Returns ``(instance_id, test_url, auth_token)`` on success, or ``None``
    if every candidate was exhausted.

    When any offer fails (create error, never-runs, bad disk), both its
    ``offer_id`` and its ``machine_id`` are blacklisted — a single flaky
    machine can host several offers, and one bad offer is strong evidence
    the others on the same machine will also fail.
    """
    offer_blacklist = set()
    machine_blacklist = set()
    attempts = 0
    last_error = ""

    def _blacklist(offer):
        offer_blacklist.add(offer["id"])
        mid = offer.get("machine_id")
        if mid is not None:
            machine_blacklist.add(mid)

    for offer in candidate_offers:
        if attempts >= max_attempts:
            log(f"Reached max launch attempts ({max_attempts})")
            break
        offer_id = offer["id"]
        if offer_id in offer_blacklist:
            continue
        machine_id = offer.get("machine_id")
        if machine_id is not None and machine_id in machine_blacklist:
            log(f"Skipping offer {offer_id} — machine {machine_id} already blacklisted")
            continue
        attempts += 1
        log(f"[Attempt {attempts}/{max_attempts}] Launching offer {offer_id} "
            f"({offer.get('gpu_name', '?')} x{offer.get('num_gpus', '?')})")

        result = api.create_instance(offer_id, payload_factory(offer))
        if not result.get("success"):
            last_error = result.get("msg") or json.dumps(result)
            log(f"  create_instance failed: {last_error}")
            _blacklist(offer)
            continue
        instance_id = result["new_contract"]
        on_instance_created(instance_id)
        log(f"  Instance {instance_id} created, waiting for startup...")

        test_url, auth_token = poll_until_running(api, instance_id)
        if not test_url:
            log(f"  Instance {instance_id} never reached running state — destroying")
            _safe_destroy(api, instance_id, log)
            _blacklist(offer)
            on_instance_created(None)
            continue

        inst = api.get_instance(instance_id)
        actual_disk = inst.get("disk_space")
        if actual_disk is None:
            log(f"  WARNING: instance {instance_id} did not report disk_space; "
                f"proceeding without verification")
        elif actual_disk < requested_disk * DISK_TOLERANCE:
            log(f"  [Bad instance] {instance_id} under-provisioned disk: "
                f"got {actual_disk:.1f} GB, requested {requested_disk:.1f} GB "
                f"— destroying, retrying")
            _safe_destroy(api, instance_id, log)
            _blacklist(offer)
            on_instance_created(None)
            continue
        else:
            log(f"  Disk OK: {actual_disk:.1f} GB / {requested_disk:.1f} GB requested")

        # Verify connectivity to the test server.  A host with broken
        # networking never answers and is abandoned within seconds; a
        # healthy host still provisioning is waited out up to
        # server_probe_timeout (see probe_test_server).
        if not probe_test_server(test_url, auth_token,
                                 server_timeout=server_probe_timeout, log=log):
            log(f"  [Bad instance] {instance_id} test server unreachable "
                f"(broken networking, or never started) — destroying, retrying")
            _safe_destroy(api, instance_id, log)
            _blacklist(offer)
            on_instance_created(None)
            continue

        return instance_id, test_url, auth_token

    log(f"Exhausted offers ({attempts} attempts, last error: {last_error or 'none'})")
    return None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Launch a Vast.ai instance from a template and stream test results."
    )
    parser.add_argument("template_hash", help="Vast template hash to test")
    parser.add_argument("--api-key", metavar="KEY", help="Vast API key (default: $VAST_API_KEY)")
    parser.add_argument("--offer", type=int, metavar="ID", help="Use specific offer ID (skip search)")
    parser.add_argument("--gpu", metavar="GPU", help="Filter to specific GPU model")
    parser.add_argument("--arch", metavar="ARCH", default="amd64",
                        help="CPU arch of the host (default: amd64). arm64 is opt-in "
                             "and untested by QA — availability/reliability not yet characterised.")
    parser.add_argument("--require-floor", action="store_true",
                        help="Fail (config_error) if the template declares no parseable "
                             "compute_cap floor — for gating runs (ADR 0005 cond 10).")
    parser.add_argument("--label", metavar="LABEL",
                        help="Stamp the launched instance with this Vast label so the "
                             "reaper can positively identify QA-owned instances.")
    parser.add_argument("--max-price", type=float, metavar="PRICE", help="Max $/hr filter")
    parser.add_argument("--disk", type=float, metavar="GB", help="Override template's recommended_disk")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TEST_TIMEOUT, metavar="SECS",
                        help=f"Max wait for tests to complete (default: {DEFAULT_TEST_TIMEOUT})")
    cleanup_group = parser.add_mutually_exclusive_group()
    cleanup_group.add_argument("--destroy", action="store_true",
                               help="Destroy instance after test (default: stop)")
    cleanup_group.add_argument("--keep", action="store_true",
                               help="Keep instance running after test (skip stop/destroy)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show selected offer and exit without launching")
    parser.add_argument("--force", action="store_true",
                        help="Skip image validation (allow non-vastai images)")
    parser.add_argument("--raw", action="store_true",
                        help="Output final JSON result to stdout (streaming output goes to stderr)")
    parser.add_argument("--log", metavar="PATH", action="append", default=[],
                        help="Tail a log file on the instance (repeatable, e.g. --log /var/log/portal --log /var/log/vllm)")
    parser.add_argument("--env", metavar="KEY=VAL", action="append", default=[],
                        help="Set instance env var (repeatable). Timeout overrides: "
                             "PROV_TIMEOUT, VLLM_HEALTH_TIMEOUT, INSTANCE_TEST_DEFAULT_TIMEOUT")
    return parser.parse_args()


def main():
    global _RAW_MODE
    args = parse_args()
    _RAW_MODE = args.raw
    api_key = args.api_key or os.environ.get("VAST_API_KEY")
    if not api_key:
        print("No API key provided. Use --api-key or set $VAST_API_KEY", file=sys.stderr)
        emit_outcome("config_error", EXIT_CONFIG_ERROR, reason="no api key")

    # All human-readable output goes to stderr so stdout is clean for --raw JSON
    def log(msg="", **kwargs):
        print(msg, file=sys.stderr, **kwargs)

    api = VastAPI(api_key)
    # Mutable state shared with signal handler, monitor thread, and the
    # retry helper — SimpleNamespace lets nested functions assign attributes.
    # ctx.instance_id tracks the currently booting or running instance so
    # the signal handler can clean up mid-retry.
    ctx = types.SimpleNamespace(panel=None, dead_reason="", instance_id=None)

    def cleanup(destroy=False):
        if ctx.panel:
            ctx.panel.cleanup()
        if ctx.instance_id is None:
            return
        if args.keep and not destroy:
            log(f"Instance {ctx.instance_id} kept running (--keep)")
            return
        try:
            if destroy or args.destroy:
                api.destroy_instance(ctx.instance_id)
                log(f"Instance {ctx.instance_id} destroyed.")
            else:
                api.stop_instance(ctx.instance_id)
                log(f"Instance {ctx.instance_id} stopped.")
        except Exception as e:
            log(f"Cleanup error: {e}")

    def sighandler(sig, frame):
        log("\nInterrupted — destroying instance...")
        cleanup(destroy=True)
        emit_outcome("interrupted", EXIT_INTERRUPTED, reason="signal")

    signal.signal(signal.SIGINT, sighandler)
    signal.signal(signal.SIGTERM, sighandler)

    # 1. Fetch template
    template = api.get_template(args.template_hash)
    image = template.get("image", "")
    tag = template.get("tag") or template.get("default_tag") or ""
    image_full = f"{image}:{tag}" if tag else image
    name = template.get("name", args.template_hash)

    # Validate image — only vastai/ and robatvastai/ images have the test suite
    ALLOWED_PREFIXES = ("vastai/", "robatvastai/")
    if "kvm" in image.lower():
        log(f"Image '{image_full}' is a KVM image — virtual machines are not supported")
        emit_outcome("config_error", EXIT_CONFIG_ERROR, reason="kvm image unsupported")
    if not args.force and not any(image.startswith(p) for p in ALLOWED_PREFIXES):
        log(f"Image '{image_full}' is not a known test-suite image "
            f"(expected {' or '.join(ALLOWED_PREFIXES)})\n"
            f"Use --force to override")
        emit_outcome("config_error", EXIT_CONFIG_ERROR, reason="non-vastai image")

    # Detect serverless templates — SERVERLESS=true appears in the env string
    # (e.g. "-e SERVERLESS=true") or in onstart (e.g. "export SERVERLESS=true").
    template_env = template.get("env", "") or ""
    template_onstart = template.get("onstart", "") or ""
    is_serverless = ("SERVERLESS=true" in template_env
                     or "SERVERLESS=true" in template_onstart)

    try:
        extra_filters = _coerce_extra_filters(template.get("extra_filters", "{}"))
    except (json.JSONDecodeError, ValueError) as e:
        emit_outcome("config_error", EXIT_CONFIG_ERROR,
                     reason=f"malformed extra_filters: {e}")

    disk = args.disk or template.get("recommended_disk_space") or 40

    log(f"Template: {name} (hash: {args.template_hash})")
    log(f"Image: {image_full}")
    log(f"Disk: {disk} GB")

    # 2. Build instance env overrides.  With template_hash_id, the server
    # uses the template's full config; we only send our additions.
    port_map = f"-p {TEST_SERVER_PORT}:{TEST_SERVER_PORT}"
    env = {"INSTANCE_TEST": "true", port_map: "1"}
    # Non-serverless templates already have OPEN_BUTTON_TOKEN=1 in their config.
    # For serverless templates we must send it as an override so the backend
    # generates an auth token (jupyter_token) we can use to connect.
    if is_serverless:
        env["OPEN_BUTTON_TOKEN"] = "1"
    if args.log:
        env["INSTANCE_TEST_SYSTEM_LOG"] = ",".join(args.log)
    for kv in args.env:
        if "=" not in kv:
            log(f"Invalid --env format (expected KEY=VAL): {kv}")
            emit_outcome("config_error", EXIT_CONFIG_ERROR, reason="bad --env format")
        k, v = kv.split("=", 1)
        env[k] = v

    # Auto-adjust client --timeout based on instance-side timeout env vars.
    # The instance runs provisioning first, then tests sequentially.  The
    # client timeout must exceed the sum of the largest sequential phases
    # so we don't give up before the instance does.
    timeout_was_default = (args.timeout == DEFAULT_TEST_TIMEOUT)
    instance_timeouts = {}
    for env_name, default in INSTANCE_TIMEOUT_ENVS.items():
        if env_name in env:
            try:
                instance_timeouts[env_name] = int(env[env_name])
            except ValueError:
                pass
        else:
            instance_timeouts[env_name] = default

    # Provisioning and the longest derivative test run sequentially.
    # INSTANCE_TEST_DEFAULT_TIMEOUT is per-test but most are fast — only
    # count it once as a floor for derivative test timeouts.
    prov = instance_timeouts.get("PROV_TIMEOUT", 3600)
    derivative = max(
        instance_timeouts.get("VLLM_HEALTH_TIMEOUT", 3600),
        instance_timeouts.get("INSTANCE_TEST_DEFAULT_TIMEOUT", 3600),
    )
    min_timeout = prov + derivative + TIMEOUT_HEADROOM

    # The test results server only binds its port after provisioning
    # finishes (boot runs 75-provisioning-manifest.sh before
    # 85-instance-test.sh), so the connectivity probe must tolerate the
    # full provisioning window once it has confirmed the host is reachable.
    server_probe_timeout = prov + TIMEOUT_HEADROOM

    if args.timeout < min_timeout:
        # Always lift a too-short timeout — keeping it (even when set explicitly)
        # makes the client give up mid-provision and report a spurious "failed",
        # a false BLOCK. A caller wanting a true cost cap should use --max-price,
        # not a timeout below the instance's own provisioning budget.
        origin = "default" if timeout_was_default else f"explicit {args.timeout}s is below"
        log(f"Auto-adjusting --timeout: {args.timeout}s → {min_timeout}s "
            f"({origin} the instance's need: prov={prov}s + derivative={derivative}s "
            f"+ headroom={TIMEOUT_HEADROOM}s)")
        args.timeout = min_timeout

    # ADR 0005 cond 10: don't trust the linter ran. On a gating run a missing or
    # unparseable compute_cap floor would silently select a random GPU generation
    # — refuse it loudly. Checked BEFORE the --offer split so --offer can't bypass it.
    required_compute_cap = _required_floor(extra_filters, "compute_cap")
    if args.require_floor and required_compute_cap is None:
        emit_outcome("config_error", EXIT_CONFIG_ERROR,
                     reason="--require-floor: template declares no parseable compute_cap floor")

    # 3. Find candidate offers.  When --offer is given, skip search and use
    # a single-element candidate list with only one allowed attempt.
    if args.offer:
        candidate_offers = [{"id": args.offer}]
        max_attempts = 1
        log(f"Using offer {args.offer}")
        if args.dry_run:
            return
    else:
        filters = {
            "verified": {"eq": True},
            "external": {"eq": False},
            "rentable": {"eq": True},
            "rented": {"eq": False},
            **extra_filters,
            # ADR 0005 cond 4: restrict to amd64 — set AFTER the extra_filters
            # spread so a template cannot override the arch guard and silently
            # land QA on an arm host.
            "cpu_arch": {"eq": args.arch},
        }
        if args.gpu:
            filters["gpu_name"] = {"eq": args.gpu}
        if args.max_price:
            filters["dph_total"] = {"lte": args.max_price}
        # Bound the VRAM search above the declared floor (don't test a small
        # claim on a huge box). Templates with an explicit lte are left alone.
        filters = apply_vram_ceiling(filters, VRAM_CEILING_MULTIPLIER)

        log(f"Filters: {format_filters(filters)}")

        # Sort: smallest VRAM above floor, then lowest compute_cap, then fanout/price.
        result = api.search_offers(filters)
        offers = result.get("offers", [])
        if not offers:
            log("No matching offers found")
            emit_outcome("no_offers", EXIT_NO_OFFERS, reason="no matching offers in VRAM/arch band")

        required_total_mb = _required_floor(extra_filters, "gpu_total_ram")
        required_per_gpu_mb = _required_floor(extra_filters, "gpu_ram")
        offers.sort(key=make_offer_sort_key(
            required_total_mb, required_per_gpu_mb, required_compute_cap))
        candidate_offers = offers[:OFFER_CANDIDATE_POOL]
        max_attempts = MAX_LAUNCH_ATTEMPTS

        top = candidate_offers[0]
        log(f"Selected offer {top['id']}: {top.get('gpu_name', '?')} x{top.get('num_gpus', '?')} "
            f"(cc {top.get('compute_cap', 0)}, arch {top.get('cpu_arch', '?')}) "
            f"@ ${top.get('dph_total', 0):.3f}/hr "
            f"(vram: {top.get('gpu_total_ram', 0)/1024:.0f} GB, "
            f"inet: {top.get('inet_down', 0):.0f}/{top.get('inet_up', 0):.0f} Mbps)")
        if required_total_mb:
            overshoot = (top.get("gpu_total_ram", 0) - required_total_mb) / required_total_mb * 100
            ceiling = required_total_mb * VRAM_CEILING_MULTIPLIER / 1024
            log(f"  Template floor: gpu_total_ram in [{required_total_mb/1024:.0f}, "
                f"{ceiling:.0f}] GB (selected {top.get('gpu_total_ram', 0)/1024:.0f} GB, "
                f"overshoot {overshoot:.0f}%, {len(candidate_offers)} candidates in retry pool)")
        if required_compute_cap:
            log(f"  Template floor: compute_cap >= {required_compute_cap:.0f} "
                f"(selected {top.get('compute_cap', 0)})")

        if args.dry_run:
            return

    # 4. Launch with retry — walk candidate_offers until one yields a running
    # instance with adequate disk.  Each failed attempt is destroyed and its
    # offer blacklisted.
    def _payload_factory(offer):
        payload = {
            "client_id": "me",
            "image": image_full,
            "disk": disk,
            "template_hash_id": args.template_hash,
            "env": env,
        }
        # Stamp a label so the reaper can positively identify QA-owned instances
        # and never touch a good non-test instance on the account.
        if args.label:
            payload["label"] = args.label
        return payload

    def _track_instance(new_id):
        ctx.instance_id = new_id

    launch = launch_with_retry(api, candidate_offers, _payload_factory, disk,
                               max_attempts, _track_instance, log,
                               server_probe_timeout=server_probe_timeout)
    if launch is None:
        log("Could not launch a usable instance on any candidate offer")
        emit_outcome("bad_instance", EXIT_BAD_INSTANCE, reason="exhausted launch attempts")

    instance_id, test_url, auth_token = launch

    # 6. Monitor instance health in background.
    # Detects if instance goes non-running, disappears, or enters a terminal state.
    # Runs through the full lifecycle (streaming + post-test polling).
    instance_dead = threading.Event()
    _dead_lock = threading.Lock()
    def _set_dead(reason):
        with _dead_lock:
            ctx.dead_reason = reason
        instance_dead.set()

    def _get_dead_reason():
        with _dead_lock:
            return ctx.dead_reason

    monitor = threading.Thread(
        target=monitor_instance_health,
        args=(api, instance_id, instance_dead, _set_dead),
        daemon=True,
    )
    monitor.start()

    # Stream SSE results
    # The server sends event types:
    #   event: output  — raw test output lines (printed to stderr)
    #   event: log     — system log lines, e.g. /var/log/portal (with --log)
    #   event: result  — final JSON summary (used for exit code + --raw stdout)
    stream_url = f"{test_url}/test-stream?log=1"
    log(f"Streaming results from {stream_url} ...\n")
    final_state = "failed"
    final_result = None

    # Split terminal: test output in top scroll region, logs in fixed bottom panel.
    # Only activates when --log is used and stderr is a TTY.
    panel = LogPanel(enabled=bool(args.log))
    ctx.panel = panel

    # Track per-test outcomes from the output stream — the runner prints
    # "─── Running: base/XX-name ───" before each test and
    # "→ PASSED", "→ FAILED", "→ SKIPPED" after each test.
    # This is more reliable than the JSON file (which has write-race issues).
    # COUPLING: uses Unicode chars ─ (U+2500) and → (U+2192) from runner.sh
    # output format.  Update both sides if the format ever changes.
    stream_counts = {"passed": 0, "failed": 0, "skipped": 0}
    stream_tests = []  # list of {"name": ..., "state": ...}
    current_test_name = None

    got_result_event = False
    for event in stream_sse(stream_url, timeout=args.timeout, token=auth_token):
        # Check if instance died unexpectedly
        if instance_dead.is_set():
            panel.cleanup()
            log(f"\nInstance {instance_id} went to '{_get_dead_reason()}' during testing")
            final_state = "error"
            break

        if event.get("_timeout"):
            panel.cleanup()
            log("\nTest timeout reached")
            break
        if event.get("_error"):
            panel.cleanup()
            # Check if the SSE error is because the instance died
            if instance_dead.is_set():
                log(f"\nInstance {instance_id} went to '{_get_dead_reason()}' during testing")
            else:
                log(f"\nSSE error: {event['_error']}")
            final_state = "error"
            break

        if event.get("_event") == "output":
            line = event.get("_data", "")
            panel.write_output(line)
            stripped = line.strip()
            # Track current test name from runner's "── Running: base/XX ──" header
            if stripped.startswith("\u2500\u2500\u2500 Running:") and stripped.endswith("\u2500\u2500\u2500"):
                current_test_name = stripped.split("Running:")[1].strip().rstrip(" \u2500")
            elif stripped.startswith("\u2192"):
                state = None
                if "PASSED" in stripped:
                    state = "passed"
                    stream_counts["passed"] += 1
                elif "FAILED" in stripped:
                    state = "failed"
                    stream_counts["failed"] += 1
                elif "SKIPPED" in stripped:
                    state = "skipped"
                    stream_counts["skipped"] += 1
                if state and current_test_name:
                    stream_tests.append({"name": current_test_name, "state": state})
                    current_test_name = None
        elif event.get("_event") == "log":
            # Log events: {"src": "portal", "line": "..."} or plain string (compat)
            if "line" in event:
                panel.write_log(event["line"], src=event.get("src", ""),
                               overwrite=event.get("overwrite", False))
            else:
                panel.write_log(event.get("_data", ""))
        elif event.get("_event") == "result":
            got_result_event = True
            final_result = event
            final_state = event.get("state", "failed")
            break

    panel.cleanup()
    # Don't stop monitor yet — we still need instance-death detection during
    # the post-test /test-status polling phase.

    # Fetch elapsed time and authoritative overall state from /test-status.
    # Don't overwrite final_result — the JSON's per-test states have a write
    # race and often show "running"; stream-tracked data is authoritative.
    elapsed = None
    if not got_result_event:
        time.sleep(2)  # give runner time to write final state
    for attempt in range(10):
        if instance_dead.is_set():
            reason = _get_dead_reason() or "unknown"
            log(f"Instance {instance_id} went to '{reason}' during post-test polling")
            final_state = "error"
            break
        try:
            status = VastAPI.get_status(test_url, token=auth_token)
            if status.get("state") in ("passed", "failed"):
                final_state = status["state"]
                elapsed = status.get("elapsed_s")
                if final_result is None:
                    final_result = status
                break
        except Exception:
            pass
        time.sleep(2)

    instance_dead.set()  # stop the monitor thread

    if final_result and elapsed is None:
        elapsed = final_result.get("elapsed_s")

    # Build authoritative per-test results from stream data.
    # Patch into final_result so --raw JSON has correct per-test states.
    if stream_tests:
        stream_test_map = {t["name"]: t["state"] for t in stream_tests}
        if final_result and "tests" in final_result:
            for t in final_result["tests"]:
                if t["name"] in stream_test_map:
                    t["state"] = stream_test_map[t["name"]]
        else:
            if final_result is None:
                final_result = {"state": final_state}
            final_result["tests"] = stream_tests

    failed_names = [t["name"] for t in stream_tests if t["state"] == "failed"]
    log(f"\n{format_summary(final_state, stream_counts, elapsed, failed_names)}")

    # The verdict CI acts on: passed only counts as a FULL pass if the runner
    # actually delivered a result event; a "passed" with got_result_event False
    # means we inferred state from a post-test poll and should be treated with
    # suspicion (ADR 0005 condition 2 — auto-promote requires both).
    exit_state, exit_code = classify_outcome(final_state)

    # --raw: emit clean JSON to stdout for programmatic consumers
    if args.raw:
        raw_output = final_result or {"state": final_state, "tests": []}
        raw_output.pop("_event", None)
        raw_output["state"] = exit_state
        raw_output["got_result_event"] = got_result_event
        raw_output["exit_code"] = exit_code
        raw_output["stream_counts"] = stream_counts
        print(json.dumps(raw_output))

    # 7. Cleanup — destroy if instance died or connection was lost
    if _get_dead_reason() or final_state == "error":
        cleanup(destroy=True)
    else:
        cleanup()
        if args.keep and instance_id:
            log(f"Instance {instance_id} still running — "
                f"'vastai stop instance {instance_id}' when done")
        elif not args.destroy and instance_id:
            log(f"Tip: 'vastai start instance {instance_id}' to restart for interactive testing")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
