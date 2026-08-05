"""VastAPI 429/5xx retry: concurrent QA shares one key, so rate-limits must be
absorbed (backoff + retry), never hard-fail the gate into config_error."""
import urllib.error

import pytest

import test_template as tt


class _FakeResp:
    def __init__(self, body=b'{"ok": true}'):
        self._b = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _http_error(code, retry_after=None):
    hdrs = {"Retry-After": retry_after} if retry_after is not None else None
    return urllib.error.HTTPError("http://x", code, "err", hdrs, None)


def test_do_request_retries_429_then_succeeds(monkeypatch):
    n = {"c": 0}

    def fake_urlopen(req, timeout=None):
        n["c"] += 1
        if n["c"] < 3:
            raise _http_error(429)
        return _FakeResp()

    monkeypatch.setattr(tt.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    out = tt.VastAPI("k")._do_request("GET", "/api/v0/instances/")
    assert out == {"ok": True}
    assert n["c"] == 3          # two 429s retried, third succeeded


def test_do_request_non_retryable_raises_immediately(monkeypatch):
    n = {"c": 0}

    def fake_urlopen(req, timeout=None):
        n["c"] += 1
        raise _http_error(404)

    monkeypatch.setattr(tt.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    with pytest.raises(urllib.error.HTTPError):
        tt.VastAPI("k")._do_request("GET", "/api/v0/instances/")
    assert n["c"] == 1          # a 404 is not retried


def test_do_request_gives_up_after_max_retries(monkeypatch):
    n = {"c": 0}

    def fake_urlopen(req, timeout=None):
        n["c"] += 1
        raise _http_error(429)

    monkeypatch.setattr(tt.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    with pytest.raises(urllib.error.HTTPError):
        tt.VastAPI("k")._do_request("GET", "/api/v0/instances/")
    assert n["c"] == tt.MAX_API_RETRIES + 1     # initial attempt + all retries, then raise


def test_retry_delay_honours_retry_after_else_exponential(monkeypatch):
    monkeypatch.setattr(tt.random, "uniform", lambda a, b: 0.0)   # strip jitter
    assert tt._retry_delay("5", 0) == 5.0            # numeric Retry-After wins
    assert tt._retry_delay(None, 3) == 8.0           # 2**3 exponential fallback
    assert tt._retry_delay("garbage", 10) == 30.0    # exponential, capped at 30
