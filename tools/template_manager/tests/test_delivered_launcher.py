"""`test_template.py --delivered` end to end, with the API, the SSH session and the
delivery faked (ADR 0068).

What must hold: the instance is launched as a customer's (no test variable, no extra
port, no results server asked); a template that cannot be reached over SSH is refused
before anything is rented; every failed delivery step is an error row with the outcome
the design gives it; and a run without on-box results is never a pass.
"""
from __future__ import annotations

import copy
import json
import sys

import pytest

import test_template as tt
import verify_proof as V

TEMPLATE_HASH = "abc123def456"
PASSING = [
    {"_event": "output", "_data": "─── Running: base/10-supervisor ───"},
    {"_event": "output", "_data": "  → PASSED (1s)"},
    {"_event": "result", "state": "passed", "elapsed_s": 4,
     "tests": [{"name": "base/10-supervisor", "state": "passed", "duration_s": 1}]},
]


def _instance(iid):
    return {"id": iid, "actual_status": "running", "public_ipaddr": "203.0.113.7",
            "ports": {"22/tcp": [{"HostPort": "40022"}]}, "disk_space": 40,
            "jupyter_token": "f00dfacecafebeef0123456789abcdef", "machine_id": 271828,
            "template_hash_id": TEMPLATE_HASH}


class FakeAPI:
    template = {}
    created = []
    destroyed = []

    def __init__(self, *a, **k):
        pass

    def get_template(self, h):
        return {"name": "QA", "image": "vastai/base-image:v1", "env": "", "runtype": "jupyter",
                "extra_filters": "{}", "recommended_disk_space": 40, **FakeAPI.template}

    def search_offers(self, filters):
        return {"offers": [{"id": 900 + n, "machine_id": 7000 + n} for n in range(3)]}

    def create_instance(self, offer_id, payload):
        FakeAPI.created.append(payload)
        return {"success": True, "new_contract": 5000 + len(FakeAPI.created)}

    def get_instance(self, iid, safe=False):
        return _instance(iid)

    get_instance_status = get_instance

    def destroy_instance(self, iid):
        FakeAPI.destroyed.append(iid)

    def stop_instance(self, iid):
        pass


class FakeSession:
    """remote.RemoteSession as the launcher uses it."""
    opened = []
    refuse = False

    def __init__(self, api, instance_id, persistent=False):
        self.instance_id, self.persistent, self.closed = instance_id, persistent, False
        FakeSession.opened.append(self)

    def __enter__(self):
        return self

    def connect(self):
        return (False, "Connection refused") if FakeSession.refuse else (True, "")

    def shared(self):
        return ("shared", self)

    def close(self):
        self.closed = True


@pytest.fixture
def launcher(monkeypatch, tmp_path, capsys):
    # a copy: main() reshapes the result event it is handed
    seen = {"atexit": [], "events": copy.deepcopy(PASSING), "external": None}
    FakeAPI.template, FakeAPI.created, FakeAPI.destroyed = {}, [], []
    FakeSession.opened, FakeSession.refuse = [], False

    def forbidden(what):
        def call(*a, **k):
            raise AssertionError(f"a delivered run used {what}")
        return call

    class FakeDelivery:
        def __init__(self, session, bundle, run_id, **kw):
            seen["delivery"] = (session, bundle, run_id, kw)
            self.report = {"bundle_digest": bundle.digest, "client_commit": bundle.commit,
                           "client_dirty": bundle.dirty, "baked_copy": "removed",
                           "environment_source": "supervisord", "layer_state": "unstamped"}

        def events(self, timeout):
            yield from seen["events"]

    def external(inst, template, tests, **kw):
        seen["external"] = kw["remote_factory"]()
        return []

    monkeypatch.setattr(sys, "stderr", sys.stderr)
    monkeypatch.setattr(tt, "_PROOF", None)
    monkeypatch.setattr(tt, "VastAPI", FakeAPI)
    monkeypatch.setattr(tt, "RemoteSession", FakeSession)
    monkeypatch.setattr(tt.delivered, "Delivery", FakeDelivery)
    monkeypatch.setattr(tt.atexit, "register", lambda fn: seen["atexit"].append(fn))
    monkeypatch.setattr(tt.signal, "signal", lambda *a: None)
    monkeypatch.setattr(tt, "monitor_instance_health", lambda *a, **k: None)
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    monkeypatch.setattr(tt, "probe_test_server", forbidden("the results-server probe"))
    monkeypatch.setattr(tt, "stream_sse", forbidden("the results-server stream"))
    monkeypatch.setattr(tt.VastAPI, "get_status", forbidden("/test-status"), raising=False)
    monkeypatch.setattr(tt, "run_external_checks", external)
    proofs = tmp_path / "proofs"

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["test_template.py", TEMPLATE_HASH, "--api-key",
                                          "vk_0123456789abcdef", "--raw", "--delivered",
                                          "--proof-dir", str(proofs), *argv])
        with pytest.raises(SystemExit) as exit_:
            tt.main()
        for fn in reversed(seen["atexit"]):
            fn()
        out, err = capsys.readouterr()
        proof_dir = sorted(proofs.iterdir())[0]
        assert V.verify(str(proof_dir)) == (V.EXIT_OK, [])
        raw = [json.loads(ln) for ln in out.splitlines() if ln.startswith("{")][-1]
        return exit_.value.code, raw, json.loads((proof_dir / "proof.json").read_text()), err
    run.seen = seen
    return run


def test_the_instance_is_launched_as_a_customers(launcher):
    code, raw, proof, _ = launcher("--log", "/var/log/portal/vllm.log")
    assert code == tt.EXIT_PASSED and raw["got_result_event"] is True
    env = FakeAPI.created[0]["env"]
    assert env == {}, "a delivered run sets no test variable and maps no port"
    # the client follows the log itself instead of asking the image to
    assert launcher.seen["delivery"][3]["logs"] == ["/var/log/portal/vllm.log"]


def test_a_passing_run_records_how_the_checks_were_delivered(launcher):
    code, raw, proof, _ = launcher()
    session, bundle, run_id, _ = launcher.seen["delivery"]
    assert session.persistent and session.instance_id == 5001
    assert run_id == proof["run"]["id"]             # the proof names the box's run directory
    assert proof["delivery"] == {
        "bundle_digest": bundle.digest, "client_commit": bundle.commit,
        "client_dirty": bundle.dirty, "baked_copy": "removed",
        "environment_source": "supervisord", "layer_state": "unstamped"}
    assert raw["delivery"] == proof["delivery"]
    assert [(t["name"], t["state"]) for t in proof["tests"]] == [("base/10-supervisor", "passed")]
    text = json.dumps(proof)
    assert "203.0.113.7" not in text and "40022" not in text and "271828" not in text


def test_the_contract_roundtrip_borrows_the_runs_session_and_teardown_closes_it(launcher):
    launcher()
    session = FakeSession.opened[0]
    assert launcher.seen["external"] == ("shared", session)
    assert len(FakeSession.opened) == 1 and session.closed


def test_a_template_without_ssh_is_refused_before_anything_is_rented(launcher):
    FakeAPI.template = {"runtype": "args"}
    code, raw, proof, err = launcher()
    assert (code, raw["state"]) == (tt.EXIT_CONFIG_ERROR, "config_error")
    assert "does not offer SSH" in raw["reason"]
    assert FakeAPI.created == [] and FakeSession.opened == []


def test_an_image_whose_suites_cannot_be_resolved_is_refused_before_renting(launcher):
    FakeAPI.template = {"image": "vastai/not-in-this-tree:v1"}
    code, raw, _, _ = launcher()
    assert code == tt.EXIT_CONFIG_ERROR and "cannot be resolved" in raw["reason"]
    assert FakeAPI.created == []


@pytest.mark.parametrize("name, final_state, code, state", [
    ("delivery/tools", "error", tt.EXIT_INSTANCE_ERROR, "instance_error"),
    ("delivery/baked-copy", "error", tt.EXIT_INSTANCE_ERROR, "instance_error"),
    ("delivery/transfer", "error", tt.EXIT_INSTANCE_ERROR, "instance_error"),
    ("delivery/start", "error", tt.EXIT_INSTANCE_ERROR, "instance_error"),
    ("delivery/busy", "error", tt.EXIT_INSTANCE_ERROR, "instance_error"),
    ("delivery/stream", "error", tt.EXIT_INSTANCE_ERROR, "instance_error"),
    ("delivery/driver-died", "error", tt.EXIT_INSTANCE_ERROR, "instance_error"),
    ("onbox/environment", "failed", tt.EXIT_FAILED, "failed"),
])
def test_a_failed_step_is_an_error_row_with_its_outcome(launcher, name, final_state, code, state):
    launcher.seen["events"] = [
        *PASSING[:2],                               # one test had reported before the step failed
        {"_delivery": {"name": name, "state": "error", "final_state": final_state,
                       "detail": "what was seen"}}]
    got, raw, proof, err = launcher()
    assert (got, raw["state"]) == (code, state)
    rows = [(t["name"], t["state"]) for t in raw["tests"]]
    assert rows == [("base/10-supervisor", "passed"), (name, "error")]
    assert [(t["name"], t["state"]) for t in proof["tests"]] == rows
    assert f"{name}: what was seen" in err


def test_ssh_refused_on_one_machine_moves_to_the_next_offer(launcher):
    refusals = iter([True, False])
    connect = FakeSession.connect
    FakeSession.connect = lambda self: ((False, "Connection refused") if next(refusals)
                                        else (True, ""))
    try:
        code, raw, _, _ = launcher()
    finally:
        FakeSession.connect = connect
    assert code == tt.EXIT_PASSED and raw["instance_id"] == 5002
    assert FakeAPI.destroyed == [5001]
    assert FakeSession.opened[0].closed, "the refused machine kept its key"


def test_ssh_refused_on_every_machine_blocks(launcher):
    FakeSession.refuse = True
    code, raw, proof, _ = launcher()
    assert (code, raw["state"]) == (tt.EXIT_INSTANCE_ERROR, "instance_error")
    assert raw["tests"] == [{"name": "delivery/ssh-unreachable", "state": "error"}]
    assert [(t["name"], t["state"]) for t in proof["tests"]] == [
        ("delivery/ssh-unreachable", "error")]
    assert len(FakeAPI.created) == 3 and FakeAPI.destroyed == [5001, 5002, 5003]


@pytest.mark.parametrize("events", [
    [{"_event": "result", "state": "passed", "tests": []}],
    [{"_event": "result", "state": "passed",
      "tests": [{"name": "base/10-supervisor", "state": "passed"}]}],     # and no output seen
])
def test_a_pass_without_on_box_results_is_not_a_pass(launcher, events):
    launcher.seen["events"] = events
    code, raw, proof, _ = launcher()
    assert (code, raw["state"]) == (tt.EXIT_INSTANCE_ERROR, "instance_error")
    assert {"name": "delivery/stream", "state": "error"} in [
        {"name": t["name"], "state": t["state"]} for t in proof["tests"]]
