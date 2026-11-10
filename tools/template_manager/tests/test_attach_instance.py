"""--instance: run the checks against an instance that is already running.

What must hold: nothing is searched or launched; the in-image results are streamed
and the external checks run as on a launched instance; the instance is NEVER stopped
or destroyed, on any exit path; and --destroy / --offer are refused at the door.
"""
from __future__ import annotations

import json
import sys

import pytest

import test_template as tt

TEMPLATE_HASH = "abc123"


def _instance(**over):
    inst = {"id": 777, "actual_status": "running", "public_ipaddr": "203.0.113.7",
            "ports": {f"{tt.TEST_SERVER_PORT}/tcp": [{"HostPort": "41000"}],
                      "8000/tcp": [{"HostPort": "40000"}]},
            "jupyter_token": "tok", "machine_id": 4242, "template_hash_id": TEMPLATE_HASH}
    inst.update(over)
    return inst


class FakeAPI:
    """Records every call. Launching, stopping or destroying fails the test."""
    calls = []
    instance = None

    def __init__(self, *a, **k):
        FakeAPI.calls = []

    def get_template(self, h):
        FakeAPI.calls.append(("get_template", h))
        return {"image": "vastai/base-image:v1", "env": "", "extra_filters": "{}",
                "recommended_disk_space": 40}

    def get_instance(self, iid, safe=False):
        FakeAPI.calls.append(("get_instance", iid))
        return FakeAPI.instance

    def get_instance_status(self, iid, safe=False):
        return FakeAPI.instance

    def _forbidden(self, *a, **k):
        raise AssertionError("an --instance run tried to search, launch, stop or destroy")

    search_offers = create_instance = stop_instance = destroy_instance = _forbidden


@pytest.fixture
def launcher(monkeypatch):
    """main() with the network faked: the stream yields one passed test."""
    seen = {"external": None, "atexit": []}
    monkeypatch.setattr(sys, "stderr", sys.stderr)          # main() wraps it; restore after
    monkeypatch.setattr(tt, "VastAPI", FakeAPI)
    monkeypatch.setattr(tt.atexit, "register", lambda fn: seen["atexit"].append(fn))
    monkeypatch.setattr(tt.signal, "signal", lambda *a: None)
    monkeypatch.setattr(tt, "probe_test_server", lambda *a, **k: True)
    monkeypatch.setattr(tt, "monitor_instance_health", lambda *a, **k: None)
    monkeypatch.setattr(tt.VastAPI, "get_status",
                        staticmethod(lambda url, token=None: {"state": "passed", "elapsed_s": 3}),
                        raising=False)

    def stream(url, timeout, token=None):
        seen["stream"] = (url, token)
        yield {"_event": "output", "_data": "─── Running: base/10-supervisor ───"}
        yield {"_event": "output", "_data": "  → PASSED"}
        yield {"_event": "result", "state": "passed",
               "tests": [{"name": "base/10-supervisor", "state": "passed"}]}
    monkeypatch.setattr(tt, "stream_sse", stream)

    def external(inst, template, tests, **kw):
        seen["external"] = (inst, tests, kw)
        return [{"name": "external/capabilities", "state": "info", "detail": "none"}]
    monkeypatch.setattr(tt, "run_external_checks", external)

    def run(*argv, instance=None):
        FakeAPI.instance = instance if instance is not None else _instance()
        monkeypatch.setattr(sys, "argv", ["test_template.py", TEMPLATE_HASH, "--api-key", "k",
                                          "--no-logs", *argv])
        with pytest.raises(SystemExit) as e:
            tt.main()
        for fn in seen["atexit"]:
            fn()                                              # every exit path's teardown
        return e.value.code, seen
    return run


def test_an_attached_instance_is_checked_and_never_torn_down(launcher, capsys):
    code, seen = launcher("--instance", "777", "--raw")
    assert code == 0
    assert seen["stream"] == ("http://203.0.113.7:41000/test-stream?log=1", "tok")
    inst, tests, kw = seen["external"]
    assert inst["id"] == 777 and kw["token"] == "tok"
    assert [t["name"] for t in tests] == ["base/10-supervisor"]
    raw = json.loads(capsys.readouterr().out)
    assert raw["instance_id"] == 777 and raw["machine_id"] == 4242
    assert not any(c[0] in ("search_offers", "create_instance") for c in FakeAPI.calls)


def test_a_failed_run_still_never_destroys_the_attached_instance(launcher, monkeypatch):
    def stream(url, timeout, token=None):
        yield {"_error": "connection lost"}
    monkeypatch.setattr(tt, "stream_sse", stream)
    monkeypatch.setattr(tt.VastAPI, "get_status", staticmethod(lambda *a, **k: {}), raising=False)
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    code, _ = launcher("--instance", "777")
    assert code != 0                                        # and no AssertionError from FakeAPI


@pytest.mark.parametrize("instance,why", [
    ({}, "not found"),
    (_instance(actual_status="exited"), "is exited"),
    (_instance(template_hash_id="other999"), "created from template other999"),
    (_instance(ports={}), "publishes no test server port"),
])
def test_an_instance_that_cannot_be_used_is_a_config_error(launcher, capsys, instance, why):
    code, _ = launcher("--instance", "777", instance=instance)
    assert code == tt.EXIT_CONFIG_ERROR
    assert why in capsys.readouterr().err


def test_an_instance_that_does_not_record_its_template_is_checked_as_given(launcher, capsys):
    inst = _instance()
    del inst["template_hash_id"]
    code, _ = launcher("--instance", "777", instance=inst)
    assert code == 0 and "does not record its template" in capsys.readouterr().err


@pytest.mark.parametrize("extra,why", [(["--destroy"], "never stops or destroys"),
                                       (["--offer", "5"], "cannot be combined")])
def test_destroy_and_offer_are_refused_with_instance(monkeypatch, capsys, extra, why):
    monkeypatch.setattr(sys, "argv", ["test_template.py", TEMPLATE_HASH, "--instance", "777",
                                      *extra])
    with pytest.raises(SystemExit) as e:
        tt.parse_args()
    assert e.value.code == tt.EXIT_CONFIG_ERROR and why in capsys.readouterr().err


def test_instance_implies_keep(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["test_template.py", TEMPLATE_HASH, "--instance", "777"])
    assert tt.parse_args().keep is True
