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


def test_poll_does_not_hit_list_endpoint_while_loading(monkeypatch):
    # If it never reaches running before the deadline, the list endpoint is
    # never touched at all — the whole (bounded) boot wait stays O(1)-per-tick.
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    monkeypatch.setattr(tt.time, "time", iter([0, 1, 2, 3, 999]).__next__)  # trip the deadline
    api = _FakeAPI(["loading", "loading", "loading"])
    url, token = tt.poll_until_running(api, 42, timeout=100)

    assert (url, token) == (None, None)
    assert api.list_calls == 0        # never fetched the heavy list while only loading


# ── loading-phase stall detection ──────────────────────────────────────────
# A host stuck mid image-pull shows a frozen status and never reaches a
# terminal state; poll must abandon the offer after stall_timeout so the retry
# loop tries another box, instead of sitting until POLL_TIMEOUT.

class _StallClock:
    """Fake clock that only advances when the code sleeps, so each poll tick
    moves time forward by exactly one POLL_INTERVAL."""
    def __init__(self, step=10.0):
        self.t = 0.0
        self.step = step

    def time(self):
        return self.t

    def sleep(self, *_):
        self.t += self.step


class _FrozenAPI:
    """get_instance_status always returns the same loading status (stuck pull);
    get_instance (the heavy list call) must never be reached."""
    def __init__(self, msg="abc123: Pulling fs layer"):
        self._s = {"actual_status": "loading", "status_msg": msg}
        self.list_calls = 0

    def get_instance_status(self, instance_id, safe=False):
        return dict(self._s)

    def get_instance(self, instance_id, safe=False):
        self.list_calls += 1
        return {"public_ipaddr": "1.2.3.4", "jupyter_token": "tok",
                "ports": {f"{tt.TEST_SERVER_PORT}/tcp": [{"HostPort": "40000"}]}}


def test_poll_abandons_stalled_loading(monkeypatch):
    clock = _StallClock()
    monkeypatch.setattr(tt.time, "time", clock.time)
    monkeypatch.setattr(tt.time, "sleep", clock.sleep)
    api = _FrozenAPI()                       # status_msg never changes
    url, token = tt.poll_until_running(api, 1, timeout=100000, stall_timeout=60)
    assert (url, token) == (None, None)      # abandoned, not waited to the deadline
    assert clock.t <= 120                    # bailed near the 60s stall window
    assert api.list_calls == 0               # never reached running


def test_poll_progress_resets_stall(monkeypatch):
    """Distinct status lines = real progress → the stall clock keeps resetting
    even past stall_timeout, and we still catch 'running'."""
    clock = _StallClock()
    monkeypatch.setattr(tt.time, "time", clock.time)
    monkeypatch.setattr(tt.time, "sleep", clock.sleep)
    seq = iter([{"actual_status": "loading", "status_msg": f"layer {i}"}
                for i in range(10)] + [{"actual_status": "running"}])

    class _Prog:
        list_calls = 0

        def get_instance_status(self, instance_id, safe=False):
            try:
                return next(seq)
            except StopIteration:
                return {"actual_status": "running"}

        def get_instance(self, instance_id, safe=False):
            type(self).list_calls += 1
            return {"public_ipaddr": "1.2.3.4", "jupyter_token": "tok",
                    "ports": {f"{tt.TEST_SERVER_PORT}/tcp": [{"HostPort": "40000"}]}}

    url, token = tt.poll_until_running(_Prog(), 1, timeout=100000, stall_timeout=30)
    assert url == "http://1.2.3.4:40000" and token == "tok"   # reached running despite >30s


def test_poll_stall_disabled_with_zero(monkeypatch):
    clock = _StallClock()
    monkeypatch.setattr(tt.time, "time", clock.time)
    monkeypatch.setattr(tt.time, "sleep", clock.sleep)
    url, _ = tt.poll_until_running(_FrozenAPI(), 1, timeout=50, stall_timeout=0)
    assert url is None                       # gave up on the deadline, not the stall
    assert clock.t >= 50
