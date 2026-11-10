"""The launcher writes a proof on EVERY exit path once a template is resolved
(ADR 0059, D10): pass, fail, config error, no offers, interrupt, timeout and crash.

main() runs against a fake API and a faked stream, as in test_attach_instance. The
functions main() registers with atexit are run afterwards in atexit's own (LIFO)
order, so each test sees exactly what a real process exit would do.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

import test_template as tt
import verify_proof as V

TEMPLATE_HASH = "abc123def456"
INSTANCE_ID = 31415926


def _instance(**over):
    inst = {"id": INSTANCE_ID, "actual_status": "running", "public_ipaddr": "203.0.113.7",
            "ports": {f"{tt.TEST_SERVER_PORT}/tcp": [{"HostPort": "41000"}],
                      "8000/tcp": [{"HostPort": "40000"}]},
            "jupyter_token": "f00dfacecafebeef0123456789abcdef", "machine_id": 271828,
            "template_hash_id": TEMPLATE_HASH, "gpu_name": "RTX A4000", "num_gpus": 1}
    inst.update(over)
    return inst


class FakeAPI:
    instance = None
    offers = []

    def __init__(self, *a, **k):
        pass

    def get_template(self, h):
        return {"name": "Proof QA", "image": "vastai/base-image:v1", "env": "",
                "extra_filters": "{}", "recommended_disk_space": 40}

    def get_instance(self, iid, safe=False):
        return FakeAPI.instance

    def get_instance_status(self, iid, safe=False):
        return FakeAPI.instance

    def search_offers(self, filters):
        return {"offers": FakeAPI.offers}

    def _forbidden(self, *a, **k):
        raise AssertionError("this test must not launch, stop or destroy")

    create_instance = stop_instance = destroy_instance = _forbidden


def _stream_passing(url, timeout, token=None):
    yield {"_event": "output", "_data": "─── Running: base/10-supervisor ───"}
    yield {"_event": "log", "src": "/var/log/portal/vllm.log",
           "line": "INFO serving on 203.0.113.7:18000"}
    yield {"_event": "output", "_data": "  → PASSED"}
    yield {"_event": "result", "state": "passed",
           "tests": [{"name": "base/10-supervisor", "state": "passed"}]}


@pytest.fixture
def launcher(monkeypatch, tmp_path, capsys):
    seen = {"atexit": [], "handlers": {}}
    monkeypatch.setattr(sys, "stderr", sys.stderr)          # main() wraps it; restore after
    monkeypatch.setattr(tt, "_PROOF", None)
    monkeypatch.setattr(tt, "VastAPI", FakeAPI)
    monkeypatch.setattr(tt.atexit, "register", lambda fn: seen["atexit"].append(fn))
    monkeypatch.setattr(tt.signal, "signal", lambda sig, fn: seen["handlers"].setdefault(sig, fn))
    monkeypatch.setattr(tt, "probe_test_server", lambda *a, **k: True)
    monkeypatch.setattr(tt, "monitor_instance_health", lambda *a, **k: None)
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    monkeypatch.setattr(tt.VastAPI, "get_status",
                        staticmethod(lambda url, token=None: {"state": "passed", "elapsed_s": 3}),
                        raising=False)
    monkeypatch.setattr(tt, "stream_sse", _stream_passing)
    monkeypatch.setattr(tt, "run_external_checks", lambda inst, template, tests, **kw: [
        {"name": "external/capabilities", "state": "info", "detail": "none (no engine)"},
        {"name": "external/x", "state": "passed", "kind": "structural",
         "detail": "via 203.0.113.7:40000"}])
    proofs = tmp_path / "proofs"

    def run(*argv, instance=None, expect_exit=True):
        FakeAPI.instance = instance if instance is not None else _instance()
        monkeypatch.setattr(sys, "argv", ["test_template.py", TEMPLATE_HASH, "--api-key",
                                          "vk_0123456789abcdef", "--no-logs", "--raw",
                                          "--proof-dir", str(proofs), *argv])
        code = None
        try:
            tt.main()
        except SystemExit as e:
            code = e.code
        except Exception as e:                  # an unhandled crash: Python would exit 1
            code = ("crashed", e)
        for fn in reversed(seen["atexit"]):                    # atexit is LIFO
            fn()
        out, err = capsys.readouterr()
        dirs = sorted(proofs.iterdir()) if proofs.exists() else []
        assert len(dirs) == 1, f"expected one proof, found {dirs}"
        rec = json.loads((dirs[0] / "proof.json").read_text())
        raw = [json.loads(ln) for ln in out.splitlines() if ln.startswith("{")]
        return code, rec, raw[-1] if raw else None, err, dirs[0]
    run.handlers = seen["handlers"]
    return run


def _common(rec, err, path):
    assert V.verify(str(path)) == (V.EXIT_OK, [])
    assert err.rstrip().splitlines()[-1] == f"Proof: {path}"       # the last line
    for name, text in ((n, open(os.path.join(r, n), encoding="utf-8").read())
                       for r, _, ns in os.walk(path) for n in ns):
        assert "203.0.113.7" not in text and "271828" not in text, name     # IP, machine id
        assert "f00dfacecafebeef" not in text, name                          # the token


def test_a_passing_run_leaves_a_proof(launcher):
    code, rec, raw, err, path = launcher("--instance", str(INSTANCE_ID))
    assert code == 0 and rec["run"]["exit_code"] == 0
    assert rec["run"]["state"] == raw["state"] == "passed"
    assert rec["tests"] == [{"name": "base/10-supervisor", "state": "passed"}]
    assert [r["name"] for r in rec["external_checks"]] == ["external/x"]
    assert rec["hardware"]["gpu_name"] == "RTX A4000"
    assert rec["run"]["flags"] == {"instance": True, "offer": False, "keep": True}
    assert rec["run"]["instance_id"] == str(INSTANCE_ID)
    assert "f00dfacecafebeef" not in json.dumps(raw), "the --raw payload carries the token"
    assert (path / "logs/vllm.log").read_text() == "INFO serving on [redacted-ip]:18000\n"
    _common(rec, err, path)


def test_a_failing_run_leaves_a_proof(launcher, monkeypatch):
    def stream(url, timeout, token=None):
        yield {"_event": "result", "state": "failed",
               "tests": [{"name": "base/10-supervisor", "state": "failed"}]}
    monkeypatch.setattr(tt, "stream_sse", stream)
    monkeypatch.setattr(tt.VastAPI, "get_status",
                        staticmethod(lambda url, token=None: {"state": "failed"}), raising=False)
    code, rec, raw, err, path = launcher("--instance", str(INSTANCE_ID))
    assert code == rec["run"]["exit_code"] == 1 and rec["run"]["state"] == raw["state"]
    _common(rec, err, path)


def test_a_config_error_after_the_template_leaves_a_proof(launcher):
    code, rec, raw, err, path = launcher("--instance", str(INSTANCE_ID), instance={})
    assert code == rec["run"]["exit_code"] == tt.EXIT_CONFIG_ERROR
    assert rec["run"]["state"] == raw["state"] == "config_error"
    assert "not found" in rec["run"]["reason"]
    _common(rec, err, path)


def test_no_offers_leaves_a_proof(launcher):
    FakeAPI.offers = []
    code, rec, raw, err, path = launcher()
    assert code == rec["run"]["exit_code"] == tt.EXIT_NO_OFFERS
    assert rec["run"]["state"] == raw["state"] == "no_offers"
    _common(rec, err, path)


def test_an_interrupt_leaves_a_proof(launcher, monkeypatch):
    def stream(url, timeout, token=None):
        yield {"_event": "output", "_data": "─── Running: base/10-supervisor ───"}
        launcher.handlers[tt.signal.SIGINT](tt.signal.SIGINT, None)
    monkeypatch.setattr(tt, "stream_sse", stream)
    code, rec, raw, err, path = launcher("--instance", str(INSTANCE_ID))
    assert code == rec["run"]["exit_code"] == tt.EXIT_INTERRUPTED
    assert rec["run"]["state"] == raw["state"] == "interrupted"
    _common(rec, err, path)


def test_a_stream_timeout_leaves_a_proof(launcher, monkeypatch):
    def stream(url, timeout, token=None):
        yield {"_timeout": True}
    monkeypatch.setattr(tt, "stream_sse", stream)
    monkeypatch.setattr(tt.VastAPI, "get_status", staticmethod(lambda *a, **k: {}), raising=False)
    code, rec, raw, err, path = launcher("--instance", str(INSTANCE_ID))
    assert code == rec["run"]["exit_code"] and code != 0
    assert rec["run"]["state"] == raw["state"]
    _common(rec, err, path)


def test_a_crash_after_the_template_leaves_a_proof(launcher, monkeypatch):
    def stream(url, timeout, token=None):
        raise RuntimeError("the stream broke in a way nobody handled")
        yield
    monkeypatch.setattr(tt, "stream_sse", stream)
    code, rec, raw, err, path = launcher("--instance", str(INSTANCE_ID))
    assert code[0] == "crashed" and isinstance(code[1], RuntimeError)
    assert rec["run"]["state"] == "crashed" and rec["run"]["exit_code"] == 1
    assert raw is None                                 # --raw never printed a verdict
    _common(rec, err, path)
