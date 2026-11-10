"""qa-gate uploads every launcher run's proof (ADR 0059, D10).

The workflow text is read and its steps are executed, as test_qa_gate_executed does:
the launcher writes into /tmp/qa-proofs, a step names the artifact uniquely per cell
and run attempt, a step appends each summary to the job summary and verifies each
proof, and the upload runs for every caller, always, with long retention.
"""
from __future__ import annotations

import json
import subprocess
import sys

import yaml

import proof as P
from test_template import redact_ips
import wfexec

TM = wfexec.REPO / "tools/template_manager"


def _steps():
    wf = yaml.safe_load(wfexec.QA_GATE.read_text())
    return {s.get("name"): s for s in wf["jobs"]["qa"]["steps"]}


def _run(script, tmp_path, env):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "python").write_text(f'#!/bin/bash\nexec {sys.executable} "$@"\n')
    (bindir / "python").chmod(0o755)
    summary, out = tmp_path / "summary.md", tmp_path / "gh_out"
    summary.write_text("")
    out.write_text("")
    r = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, capture_output=True, text=True,
                       env={"PATH": f"{bindir}:/usr/bin:/bin", "GITHUB_STEP_SUMMARY": str(summary),
                            "GITHUB_OUTPUT": str(out), **env})
    return r, summary.read_text(), out.read_text()


def test_the_launcher_writes_its_proofs_where_the_upload_reads():
    run = _steps()["Run live test (DESTROY the instance after)"]["run"]
    assert "--proof-dir /tmp/qa-proofs" in run


def test_every_caller_uploads_its_proofs_always_for_ninety_days():
    up = _steps()["Upload test proofs"]
    assert up["uses"].startswith("actions/upload-artifact@")
    assert up["if"].startswith("always()") and "evidence_name" not in up["if"]
    assert up["with"]["path"] == "/tmp/qa-proofs"
    assert up["with"]["retention-days"] == 90
    assert up["with"]["name"] == "${{ steps.proofname.outputs.name }}"


def test_the_verdict_evidence_uploads_the_stripped_copy():
    """The promote workflows read qa-raw.json at the artifact root: the upload is the
    stripped copy, under the same file name."""
    steps = _steps()
    up = steps["Upload verdict evidence"]
    assert up["with"]["path"] == "/tmp/qa-evidence/qa-raw.json" and up["with"]["retention-days"] == 7
    prep = steps["Prepare verdict evidence for upload (no machine id)"]
    assert prep["if"] == up["if"]
    assert "public_evidence.py\" /tmp/qa-raw.json /tmp/qa-evidence/qa-raw.json" in prep["run"]
    names = list(steps)
    assert names.index("Run live test (DESTROY the instance after)") < names.index(
        "Prepare verdict evidence for upload (no machine id)") < names.index("Upload verdict evidence")


def test_the_loop_still_reads_machine_id_from_the_jobs_own_payload():
    """The redraw keeps off a failed host using machine_id from the job's whole copy."""
    run = _steps()["Run live test (DESTROY the instance after)"]["run"]
    assert "jq -r '.machine_id // empty' /tmp/qa-raw-last.json" in run
    assert "/tmp/qa-evidence" not in run


def test_the_prepare_step_strips_machine_id_and_keeps_the_verdict(tmp_path):
    import public_evidence
    import qa_verdict
    raw = {"state": "passed", "exit_code": 0, "got_result_event": True,
           "tests": [{"name": "base/60-gpu-cuda", "state": "passed"}],
           "machine_id": 271828, "instance_id": 31415926,
           "ssh": {"host": "203.0.113.9", "port": 40022},
           "external_checks": [{"name": "x", "state": "passed", "auth_token": "f00dface"}]}
    src = tmp_path / "qa-raw.json"
    src.write_text("narration that leaked to stdout\n" + json.dumps(raw) + "\n")
    script = _steps()["Prepare verdict evidence for upload (no machine id)"]["run"]
    script = script.replace("/tmp/qa-raw.json", str(src)).replace(
        "/tmp/qa-evidence", str(tmp_path / "evidence"))
    r, _, _ = _run(script, tmp_path, {"TM": str(TM)})
    assert r.returncode == 0, r.stderr
    out = (tmp_path / "evidence/qa-raw.json").read_text()
    assert "machine_id" not in out and "271828" not in out and "40022" not in out
    assert "f00dface" not in out and "narration" not in out
    doc = json.loads(out.splitlines()[-1])
    assert doc["instance_id"] == 31415926
    assert qa_verdict.classify(0, doc, ["base/60-gpu-cuda"]) == qa_verdict.classify(
        0, raw, ["base/60-gpu-cuda"])
    assert public_evidence.main([str(tmp_path / "missing.json"), str(tmp_path / "o.json")]) == 0
    assert not (tmp_path / "o.json").exists()


def _name(tmp_path, **env):
    step = _steps()["Name the test proof artifact"]
    r, _, out = _run(step["run"], tmp_path, {"GITHUB_RUN_ATTEMPT": "1", **env})
    assert r.returncode == 0, r.stderr
    return out.strip().split("=", 1)[1]


def test_the_artifact_name_is_unique_per_cell_and_attempt_and_safe(tmp_path):
    a = _name(tmp_path, CELL_INPUTS='{"repo":"vastai/vllm","tag":"v1","template_dir":"a"}',
              QA_REPO="vastai/vllm", QA_TAG="v1:cuda-13")
    b = _name(tmp_path, CELL_INPUTS='{"repo":"vastai/vllm","tag":"v1","template_dir":"b"}',
              QA_REPO="vastai/vllm", QA_TAG="v1:cuda-13")
    c = _name(tmp_path, CELL_INPUTS='{"repo":"vastai/vllm","tag":"v1","template_dir":"a"}',
              QA_REPO="vastai/vllm", QA_TAG="v1:cuda-13", GITHUB_RUN_ATTEMPT="2")
    assert len({a, b, c}) == 3
    assert a.startswith("qa-proof-vastai-vllm-v1-cuda-13-") and a.endswith("-attempt1")
    assert not any(ch in a for ch in '/:"<>|*?\\')
    assert _name(tmp_path, CELL_INPUTS="{}", EVIDENCE="qa-evidence-x").startswith(
        "qa-proof-qa-evidence-x-")


def _proof(root, state):
    rec = P.ProofRecorder(str(root), ["abc"], redact_ips, env={},
                          git_state=lambda p: ("unknown", "unknown"))
    rec.begin("abc123", {"name": "T"}, "vastai/base-image:v1", [], {})
    rec.set_outcome(state, 0 if state == "passed" else 1)
    return rec.write()


def test_every_proof_is_summarised_and_verified(tmp_path):
    root = tmp_path / "qa-proofs"
    _proof(root, "passed")
    bad = _proof(root, "failed")
    (tmp_path / "x").mkdir()
    with open(f"{bad}/summary.md", "a") as f:
        f.write("tampered\n")
    script = _steps()["Summarise and verify the test proofs"]["run"].replace(
        "/tmp/qa-proofs", str(root))
    r, summary, _ = _run(script, tmp_path, {"TM": str(TM)})
    assert r.returncode == 0, r.stderr
    assert summary.count("### Test proof") == 2
    assert "**passed**" in summary and "**failed**" in summary
    assert r.stdout.count("::warning::test proof") == 1          # the tampered one


def test_no_proof_is_a_warning_not_a_failure(tmp_path):
    script = _steps()["Summarise and verify the test proofs"]["run"].replace(
        "/tmp/qa-proofs", str(tmp_path / "none"))
    r, summary, _ = _run(script, tmp_path, {"TM": str(TM)})
    assert r.returncode == 0 and "no test proof was written" in r.stdout and summary == ""
