"""ADR 0009 teardown discipline (the money property), after the red-team/review hardening:
a billing box is ALWAYS recorded (provisional ledger from the QA-INSTANCE-CREATED marker,
before any verdict) and torn down on every non-hold verdict; the box is held ONLY on a real
app-failure (exit 1); the label stays inside the scheduled reaper's scope."""
import json
import os
import sys
import types
from pathlib import Path

import imagegen.qa as q

_TM = Path(__file__).resolve().parents[2] / "template_manager"


def _fake_repo(tmp_path, name="myimg"):
    d = tmp_path / "derivatives/pytorch/derivatives" / name
    (d / "templates" / f"{name}-qa").mkdir(parents=True, exist_ok=True)
    (d / "templates" / f"{name}-qa" / "template.yml").write_text("name: x\n")
    return tmp_path, d


def _fakes(monkeypatch, tmp_path, verdict, created_id, torn, returncode=None):
    monkeypatch.setattr(q, "_REPO", tmp_path)
    monkeypatch.setenv("VAST_API_KEY", "k")
    monkeypatch.setenv("DOCKERHUB_NAMESPACE_STAGING", "ns")

    def fake_run(cmd, **kw):
        parts = [str(c) for c in cmd]
        if "create.py" in " ".join(parts) and "--delete" not in parts:
            Path(parts[parts.index("--emit-result") + 1]).write_text(json.dumps([{"hash_id": "h1", "id": "tid1"}]))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_run_test(args, qa_dir, label, log):
        if created_id is not None:                       # simulate the marker -> provisional ledger
            q._write_ledger(qa_dir, created_id, label)
        rc = returncode if returncode is not None else verdict.get("exit_code", 1)
        return rc, json.dumps(verdict), created_id

    def fake_teardown(ak, iid, qd, lg):                  # simulate the confirmed-destroy path
        torn.append(iid)
        (qd / "teardown-ledger.json").unlink(missing_ok=True)

    monkeypatch.setattr(q, "_run", fake_run)
    monkeypatch.setattr(q, "_run_test", fake_run_test)
    monkeypatch.setattr(q, "_teardown", fake_teardown)


def test_exit1_holds_bundle_and_ledger_no_teardown(tmp_path, monkeypatch):
    tmp_path, img = _fake_repo(tmp_path); torn = []
    v = {"state": "failed", "exit_code": 1, "got_result_event": True,
         "instance_id": 999, "ssh": {"host": "h", "port": 40022}}
    _fakes(monkeypatch, tmp_path, v, "999", torn)
    assert q.run("myimg", log=lambda m: None) == 1
    assert (img / ".qa" / "bundle.json").is_file()
    assert (img / ".qa" / "teardown-ledger.json").is_file(), "held box must keep its ledger"
    assert not torn, "a FAILED (exit 1) box is HELD for diagnosis, not torn down"


def test_pass_tears_down_clears_ledger_no_bundle(tmp_path, monkeypatch):
    tmp_path, img = _fake_repo(tmp_path); torn = []
    v = {"state": "passed", "exit_code": 0, "got_result_event": True, "instance_id": 42}
    _fakes(monkeypatch, tmp_path, v, "42", torn)
    assert q.run("myimg", log=lambda m: None) == 0
    assert torn == [42]
    assert not (img / ".qa" / "bundle.json").exists()
    assert not (img / ".qa" / "teardown-ledger.json").exists()


def test_instance_error_tears_down_no_false_hold(tmp_path, monkeypatch):
    """exit 5: the box was created (ledger written) then test_template destroyed it — the
    launcher must NOT claim 'held', and must tear down (404-safe covers already-gone)."""
    tmp_path, img = _fake_repo(tmp_path); torn = []
    v = {"state": "instance_error", "exit_code": 5, "got_result_event": False, "instance_id": 77}
    _fakes(monkeypatch, tmp_path, v, "77", torn)
    assert q.run("myimg", log=lambda m: None) == 5
    assert torn == [77]
    assert not (img / ".qa" / "bundle.json").exists()


def test_config_error_after_launch_recovers_box_id_from_marker(tmp_path, monkeypatch):
    """The red-team's fatal case: --raw carries NO instance_id on an early-exit-after-launch,
    but the QA-INSTANCE-CREATED marker did — so the box is still recorded and torn down."""
    tmp_path, img = _fake_repo(tmp_path); torn = []
    v = {"state": "config_error", "exit_code": 4}        # NO instance_id in the verdict
    _fakes(monkeypatch, tmp_path, v, "555", torn)        # ...but the marker gave us 555
    assert q.run("myimg", log=lambda m: None) == 4
    assert torn == ["555"], "box id must be recovered from the marker and torn down"


def test_exit_code_disagreement_never_passes(tmp_path, monkeypatch):
    tmp_path, img = _fake_repo(tmp_path); torn = []
    v = {"state": "passed", "exit_code": 0, "got_result_event": True, "instance_id": 9}
    _fakes(monkeypatch, tmp_path, v, "9", torn, returncode=1)   # payload says 0, process says 1
    assert q.run("myimg", log=lambda m: None) != 0, "must never PASS when the process code disagrees"


def test_run_test_writes_provisional_ledger_from_marker(tmp_path, monkeypatch):
    qa_dir = tmp_path / ".qa"; qa_dir.mkdir()

    class FakeProc:
        def __init__(self):
            self.stderr = iter(["booting\n", "QA-INSTANCE-CREATED 12345\n", "running\n"])
            self.stdout = types.SimpleNamespace(read=lambda: '{"state":"failed","exit_code":1}\n')
            self.returncode = 1

        def wait(self):
            pass

    monkeypatch.setattr(q.subprocess, "Popen", lambda *a, **k: FakeProc())
    rc, out, cid = q._run_test(["x"], qa_dir, "base-image-qa-imagegen-x", log=lambda m: None)
    assert cid == "12345" and rc == 1
    led = json.loads((qa_dir / "teardown-ledger.json").read_text())
    assert led["instance_id"] == "12345", "ledger written from the marker, before the verdict"


def test_load_dotenv_handles_export_and_comments(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('export VAST_API_KEY=abc123\nDOCKERHUB_NAMESPACE_STAGING=ns # note\n')
    monkeypatch.delenv("VAST_API_KEY", raising=False)
    monkeypatch.delenv("DOCKERHUB_NAMESPACE_STAGING", raising=False)
    q._load_dotenv(tmp_path)
    assert os.environ["VAST_API_KEY"] == "abc123"                  # `export ` stripped
    assert os.environ["DOCKERHUB_NAMESPACE_STAGING"] == "ns"       # inline comment stripped


def test_launcher_label_is_inside_reaper_scope():
    sys.path.insert(0, str(_TM))
    import reap_orphans
    assert reap_orphans.in_scope({"label": f"{q._LABEL_PREFIX}-chatterbox"}, "base-image-qa", None) is True
    assert reap_orphans.in_scope({"label": "someones-dev-box"}, "base-image-qa", None) is False


def test_ssh_reachable_reflects_probe(monkeypatch):
    """The held-box SSH probe reflects reachability, RETRIES before failing (Vast SSH is
    flaky on first connect), and is False without coords."""
    monkeypatch.setattr(q.time, "sleep", lambda s: None)   # don't actually wait between retries
    monkeypatch.setattr(q, "_run", lambda cmd, **k: types.SimpleNamespace(returncode=0))
    assert q._ssh_reachable({"host": "h", "port": 22}) is True

    # transient then success -> reachable (proves the retry)
    calls = {"n": 0}
    def flaky(cmd, **k):
        calls["n"] += 1
        return types.SimpleNamespace(returncode=0 if calls["n"] >= 2 else 255)
    monkeypatch.setattr(q, "_run", flaky)
    assert q._ssh_reachable({"host": "h", "port": 22}) is True and calls["n"] == 2

    monkeypatch.setattr(q, "_run", lambda cmd, **k: types.SimpleNamespace(returncode=255))
    assert q._ssh_reachable({"host": "h", "port": 22}, attempts=2) is False
    assert q._ssh_reachable({}) is False


def test_build_pytorch_nested_passes_ref_and_persists(tmp_path, monkeypatch):
    tmp_path, img = _fake_repo(tmp_path, "chatterbox")
    (img / "Dockerfile").write_text("ARG CHATTERBOX_REF\nRUN echo hi\n")
    monkeypatch.setattr(q, "_REPO", tmp_path)
    monkeypatch.setenv("DOCKERHUB_NAMESPACE_STAGING", "ns")
    calls = []
    monkeypatch.setattr(q, "_run", lambda cmd, **k: (calls.append([str(c) for c in cmd]),
                                                     types.SimpleNamespace(returncode=0))[1])
    q.build("chatterbox", ref="v2.0.0", tag="robatvastai/chatterbox:latest", push=True, log=lambda m: None)
    build_cmd = calls[0]
    assert "CHATTERBOX_REF=v2.0.0" in build_cmd and "robatvastai/chatterbox:latest" in build_cmd
    assert calls[1][:2] == ["docker", "push"]
    state = json.loads((img / ".qa" / "build.json").read_text())
    assert state == {"ref": "v2.0.0", "tag": "robatvastai/chatterbox:latest"}


def test_build_external_adds_context_and_reuses_persisted(tmp_path, monkeypatch):
    tmp_path, img = _fake_repo(tmp_path, "myext")
    (img / "Dockerfile").write_text("FROM x\nCOPY --from=base_image_source /ROOT /\n")
    (img / ".qa").mkdir()
    (img / ".qa" / "build.json").write_text(json.dumps({"ref": None, "tag": "ns/myext:latest"}))
    monkeypatch.setattr(q, "_REPO", tmp_path)
    monkeypatch.setenv("DOCKERHUB_NAMESPACE_STAGING", "ns")
    calls = []
    monkeypatch.setattr(q, "_run", lambda cmd, **k: (calls.append([str(c) for c in cmd]),
                                                     types.SimpleNamespace(returncode=0))[1])
    q.build("myext", log=lambda m: None)                       # no ref/tag -> reuse persisted
    build_cmd = calls[0]
    assert any("base_image_source=" in c for c in build_cmd)   # external graft context
    assert "--build-arg" not in build_cmd                      # no <NAME>_REF for external
    assert "ns/myext:latest" in build_cmd


def test_ensure_repo_creates_public_when_missing(monkeypatch):
    monkeypatch.setattr(q, "_dockerhub_creds", lambda: ("user", "pw"))
    calls = []
    def fake_hub(method, path, token=None, body=None):
        calls.append((method, path, body))
        if path == "/v2/users/login/":
            return 200, {"token": "jwt"}
        if path.startswith("/v2/repositories/robatvastai/foo"):
            return 404, {}                      # repo missing
        if path == "/v2/repositories/":
            return 201, {}                      # created
        return None, {}
    monkeypatch.setattr(q, "_hub_req", fake_hub)
    logs = []
    q._ensure_repo("robatvastai/foo", logs.append)
    create = [c for c in calls if c[1] == "/v2/repositories/"]
    assert create and create[0][2]["is_private"] is False and create[0][2]["name"] == "foo"
    assert any("created PUBLIC" in m for m in logs)


def test_ensure_repo_skips_non_dockerhub_and_missing_creds(monkeypatch):
    monkeypatch.setattr(q, "_hub_req",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call")))
    q._ensure_repo("ghcr.io/org/name", lambda m: None)     # ns has '.', 2 slashes -> early no-op
    monkeypatch.setattr(q, "_dockerhub_creds", lambda: None)
    logs = []
    q._ensure_repo("ns/foo", logs.append)                  # no creds -> warn, no hub call
    assert any("no docker-login creds" in m for m in logs)
