"""poll_until_running must poll status cheaply (by-id) every tick and hit the
heavy list-all endpoint only ONCE, when the instance is running — otherwise the
boot poll hammers the list endpoint that 429s under concurrent QA."""
import test_template as tt


class _FakeAPI:
    def __init__(self, statuses):
        self._statuses = iter(statuses)
        self.status_calls = 0     # cheap by-id polls
        self.list_calls = 0       # heavy list-all fetches

    def get_instance_status(self, instance_id, safe=False):
        self.status_calls += 1
        try:
            return {"actual_status": next(self._statuses)}
        except StopIteration:
            return {"actual_status": "running"}

    def get_instance(self, instance_id, safe=False):
        self.list_calls += 1
        return {
            "public_ipaddr": "1.2.3.4",
            "jupyter_token": "tok",
            "ports": {f"{tt.TEST_SERVER_PORT}/tcp": [{"HostPort": "40000"}]},
        }


def test_poll_uses_byid_per_tick_and_list_once(monkeypatch):
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    api = _FakeAPI(["loading", "loading", "running"])
    url, token = tt.poll_until_running(api, 42, timeout=100)

    assert url == "http://1.2.3.4:40000"
    assert token == "tok"
    assert api.status_calls == 3      # by-id polled every tick during boot
    assert api.list_calls == 1        # heavy list-all fetched exactly once (at running)


class _FakeAPIWithMsg:
    """Reports a status_msg alongside actual_status (the by-id poll's real shape),
    so a bad-status message can be exercised. Stays 'loading' forever — only the
    message should end the poll."""
    def __init__(self, status_msg, actual_status="loading"):
        self._status_msg = status_msg
        self._actual_status = actual_status
        self.list_calls = 0

    def get_instance_status(self, instance_id, safe=False):
        return {"actual_status": self._actual_status, "status_msg": self._status_msg}

    def get_instance(self, instance_id, safe=False):
        self.list_calls += 1
        return {"public_ipaddr": "1.2.3.4", "jupyter_token": "tok", "ports": {}}


def test_poll_fastfails_on_unexpected_eof(monkeypatch):
    # A broken host truncates the image pull ("unexpected EOF while reading") and
    # never leaves 'loading'. poll_until_running must fail fast on the message
    # (return None,None) so the caller destroys + blacklists + retries the next
    # offer — NOT wait out the 40-min POLL_TIMEOUT. Regression: this signature
    # was absent from BAD_STATUS_PATTERNS and leaked stuck instances on a bad host.
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    # A generous deadline: if the fix regresses, the loop would spin here, not exit.
    monkeypatch.setattr(tt.time, "time", iter([0, 1, 2, 3, 4, 5]).__next__)
    api = _FakeAPIWithMsg("failed to register layer: unexpected EOF while reading")
    url, token = tt.poll_until_running(api, 42, timeout=100)

    assert (url, token) == (None, None)   # fast-failed on the message
    assert api.list_calls == 0            # never reached running; no heavy fetch


def test_poll_does_not_hit_list_endpoint_while_loading(monkeypatch):
    # If it never reaches running before the deadline, the list endpoint is
    # never touched at all — the whole (bounded) boot wait stays O(1)-per-tick.
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    monkeypatch.setattr(tt.time, "time", iter([0, 1, 2, 3, 999]).__next__)  # trip the deadline
    api = _FakeAPI(["loading", "loading", "loading"])
    url, token = tt.poll_until_running(api, 42, timeout=100)

    assert (url, token) == (None, None)
    assert api.list_calls == 0        # never fetched the heavy list while only loading
