"""ADR 0009 teardown discipline (the money-losing property): a held box is always
recoverable — the ledger is written the instant it's held, the box is NOT torn down while
diagnosis is pending, PASS tears it down, and the stamped label is inside the scheduled
reaper's scope so a crashed run is reaped regardless."""
import json
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


def _install_fakes(monkeypatch, tmp_path, verdict, torn):
    monkeypatch.setattr(q, "_REPO", tmp_path)
    monkeypatch.setenv("VAST_API_KEY", "k")
    monkeypatch.setenv("DOCKERHUB_NAMESPACE_STAGING", "ns")

    def fake_run(cmd, **kw):
        s = " ".join(str(c) for c in cmd)
        if "create.py" in s and "--delete" not in s:      # publish -> write emit-result
            parts = [str(c) for c in cmd]
            out = Path(parts[parts.index("--emit-result") + 1])
            out.write_text(json.dumps([{"hash_id": "h1", "id": "tid1", "name": name_of(s)}]))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "test_template.py" in s:                        # run -> emit --raw verdict
            return types.SimpleNamespace(returncode=verdict["exit_code"],
                                         stdout=json.dumps(verdict) + "\n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")  # --delete

    monkeypatch.setattr(q, "_run", fake_run)
    monkeypatch.setattr(q, "_teardown", lambda *a, **k: torn.append(a[1]))  # record instance_id, don't destroy


def name_of(_s):
    return "myimg"


def test_block_holds_writes_ledger_and_bundle_no_teardown(tmp_path, monkeypatch):
    tmp_path, img = _fake_repo(tmp_path)
    torn = []
    verdict = {"state": "failed", "exit_code": 1, "got_result_event": True,
               "instance_id": 999, "ssh": {"host": "h", "port": 40022}}
    _install_fakes(monkeypatch, tmp_path, verdict, torn)

    rc = q.run("myimg", log=lambda m: None)

    assert rc == 1
    ledger = json.loads((img / ".qa" / "teardown-ledger.json").read_text())
    assert ledger["instance_id"] == 999                      # box addressable for recovery
    assert ledger["label"].startswith("base-image-qa")       # inside the reaper's scope
    assert not torn, "a BLOCKED box must be HELD for diagnosis, not torn down"
    assert (img / ".qa" / "bundle.json").is_file()           # skill handoff written


def test_pass_tears_down_clears_ledger_no_bundle(tmp_path, monkeypatch):
    tmp_path, img = _fake_repo(tmp_path)
    torn = []
    verdict = {"state": "passed", "exit_code": 0, "got_result_event": True, "instance_id": 42}
    _install_fakes(monkeypatch, tmp_path, verdict, torn)

    rc = q.run("myimg", log=lambda m: None)

    assert rc == 0
    assert torn == [42], "PASS must tear the box down"
    assert not (img / ".qa" / "bundle.json").exists()


def test_launcher_label_is_inside_reaper_scope():
    """The stamped label must be prefix-covered by reap_orphans' base-image-qa scope, or a
    crashed run leaks a paid GPU (ADR 0009 cond 4)."""
    sys.path.insert(0, str(_TM))
    import reap_orphans
    inst = {"label": f"{q._LABEL_PREFIX}-chatterbox"}
    assert reap_orphans.in_scope(inst, "base-image-qa", None) is True
    assert reap_orphans.in_scope({"label": "someones-dev-box"}, "base-image-qa", None) is False
