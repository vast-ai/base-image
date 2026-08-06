"""stream_sse must survive a mid-run drop (ADR 0019 W6 finding).

Observed live on a cuda-11.8 QA cell: the SSE stream ended cleanly during
base/26-caddy-auth — a test that restarts Caddy three times — while the instance
was healthy and went on to finish all 23 tests. The client stopped listening,
never saw the runner's result event, fell back to a status poll, and the verdict
became "passed without a result event" = BLOCK. An infra blip was reported as an
untrustworthy artifact, which is exactly the flake-then-bypass dynamic that kills
a gate.

These drive a real HTTP server that hangs up mid-stream, so they exercise the
socket path rather than a mocked generator.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from test_template import MAX_STREAM_RECONNECTS, stream_sse


def _serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_port}/test-stream"


def _sse(body: str) -> bytes:
    return body.encode()


class _DropOnceThenFinish(BaseHTTPRequestHandler):
    """First connection: two outputs then hang up. Second: the result event."""
    connections = 0

    def do_GET(self):
        type(self).connections += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if type(self).connections == 1:
            self.wfile.write(_sse(
                'event: output\ndata: {"_data": "line-before-drop"}\n\n'))
            self.wfile.flush()
            self.close_connection = True          # the drop
            return
        self.wfile.write(_sse(
            'event: output\ndata: {"_data": "line-after-reconnect"}\n\n'
            'event: result\ndata: {"state": "passed"}\n\n'))
        self.wfile.flush()

    def log_message(self, *a):
        pass


class _AlwaysDrops(BaseHTTPRequestHandler):
    connections = 0

    def do_GET(self):
        type(self).connections += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(_sse('event: output\ndata: {"_data": "x"}\n\n'))
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *a):
        pass


def test_reconnects_and_receives_the_result_event_after_a_drop():
    """THE regression: a drop mid-run must not cost us the result event."""
    _DropOnceThenFinish.connections = 0
    srv, url = _serve(_DropOnceThenFinish)
    try:
        events = list(stream_sse(url, timeout=30))
    finally:
        srv.shutdown()

    assert any(e.get("_reconnect") for e in events), "no reconnect was attempted"
    results = [e for e in events if e.get("_event") == "result"]
    assert results, "result event never received — the verdict would be 'passed without a result event'"
    assert results[0]["state"] == "passed"
    assert _DropOnceThenFinish.connections == 2


def test_output_from_both_sides_of_the_gap_is_delivered():
    _DropOnceThenFinish.connections = 0
    srv, url = _serve(_DropOnceThenFinish)
    try:
        data = [e.get("_data") for e in stream_sse(url, timeout=30)
                if e.get("_event") == "output"]
    finally:
        srv.shutdown()
    assert "line-before-drop" in data
    assert "line-after-reconnect" in data


def test_reconnects_are_bounded_and_then_give_up():
    """A genuinely dead endpoint must terminate, not loop to the deadline."""
    _AlwaysDrops.connections = 0
    srv, url = _serve(_AlwaysDrops)
    try:
        events = list(stream_sse(url, timeout=120))
    finally:
        srv.shutdown()
    reconnects = [e for e in events if e.get("_reconnect")]
    assert len(reconnects) == MAX_STREAM_RECONNECTS, (
        f"expected exactly {MAX_STREAM_RECONNECTS} bounded reconnects, got {len(reconnects)}"
    )
    assert _AlwaysDrops.connections == MAX_STREAM_RECONNECTS + 1


def test_reconnect_events_carry_a_reason():
    """The operator needs to tell a service restart from a dead box."""
    _DropOnceThenFinish.connections = 0
    srv, url = _serve(_DropOnceThenFinish)
    try:
        rc = [e for e in stream_sse(url, timeout=30) if e.get("_reconnect")]
    finally:
        srv.shutdown()
    assert rc and rc[0].get("_reason")


def test_a_clean_run_makes_no_reconnect_attempt():
    """No behaviour change on the happy path."""
    class _Clean(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(_sse('event: result\ndata: {"state": "passed"}\n\n'))
            self.wfile.flush()

        def log_message(self, *a):
            pass

    srv, url = _serve(_Clean)
    try:
        events = list(stream_sse(url, timeout=30))
    finally:
        srv.shutdown()
    assert not any(e.get("_reconnect") for e in events)
    assert any(e.get("_event") == "result" for e in events)
