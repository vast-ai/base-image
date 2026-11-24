"""qa-gate's `require_external` input reaches qa_verdict only when a caller sets it.

The Verdict step is executed, as test_qa_gate_proofs does: its `${{ }}` inputs are
bound explicitly (never guessed), `python` is a wrapper that records its argv and
runs the real qa_verdict.py, and the assertions are on the command it ran and the
verdict it wrote. Empty must leave the command exactly as it was before the input
existed; non-empty adds `--external-verdict --require-external <value>`.
"""
from __future__ import annotations

import json
import subprocess
import sys

import yaml

import wfexec

TM = wfexec.REPO / "tools/template_manager"
_BIND = {"steps.test.outputs.exit_code": "0", "inputs.require_tests": "base/60-gpu-cuda",
         "github.event_name": "push"}
RAW = {"state": "passed", "exit_code": 0, "got_result_event": True,
       "tests": [{"name": "base/60-gpu-cuda", "state": "passed"}],
       "external_checks": [{"name": "external/capabilities", "state": "info"},
                           {"name": "external/chat-inference", "state": "passed",
                            "kind": "structural"}]}


def _verdict_step():
    wf = yaml.safe_load(wfexec.QA_GATE.read_text())
    return next(s for s in wf["jobs"]["qa"]["steps"] if s.get("id") == "verdict")


def _run(tmp_path, require_external):
    raw = tmp_path / "qa-raw.json"
    raw.write_text("narration\n" + json.dumps(RAW) + "\n")
    script = _verdict_step()["run"]
    for expr, val in _BIND.items():
        script = script.replace("${{ " + expr + " }}", val)
    assert "${{" not in script, "unresolved expression — bind it, never guess"
    script = script.replace("/tmp/qa-raw.json", str(raw))
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    argv = tmp_path / "argv.json"
    (bindir / "python").write_text(
        "#!/bin/bash\n"
        f"{sys.executable} -c 'import json,sys; json.dump(sys.argv[1:], open(\"{argv}\", \"w\"))' \"$@\"\n"
        f'exec {sys.executable} "$@"\n')
    (bindir / "python").chmod(0o755)
    out = tmp_path / "gh_out"
    out.write_text("")
    r = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, capture_output=True, text=True,
                       env={"PATH": f"{bindir}:/usr/bin:/bin", "GITHUB_OUTPUT": str(out),
                            "TM": str(TM), "REQUIRE_EXTERNAL": require_external})
    verdict = [ln.split("=", 1)[1] for ln in out.read_text().splitlines() if ln.startswith("verdict=")]
    return r, json.loads(argv.read_text()), verdict[-1] if verdict else None


def _today(raw):
    """The verdict command as it was before `require_external` existed."""
    return [f"{TM}/qa_verdict.py", "--exit-code", "0", "--raw", str(raw),
            "--require-tests", "base/60-gpu-cuda"]


def test_the_input_reaches_the_step_through_env_and_defaults_to_empty():
    wf = yaml.safe_load(wfexec.QA_GATE.read_text())
    inputs = (wf.get("on") or wf[True])["workflow_call"]["inputs"]
    assert inputs["require_external"]["default"] == "" and inputs["require_external"]["type"] == "string"
    step = _verdict_step()
    assert step["env"]["REQUIRE_EXTERNAL"] == "${{ inputs.require_external }}"
    assert "inputs.require_external" not in step["run"], "spliced into bash instead of via env"


def test_an_empty_input_leaves_the_verdict_command_unchanged(tmp_path):
    r, argv, verdict = _run(tmp_path, "")
    assert r.returncode == 0, r.stderr
    assert argv == _today(tmp_path / "qa-raw.json")
    assert verdict == "pass"


def test_spaces_and_commas_alone_name_nothing_and_change_nothing(tmp_path):
    r, argv, verdict = _run(tmp_path, " , \n ")
    assert r.returncode == 0, r.stderr
    assert argv == _today(tmp_path / "qa-raw.json") and verdict == "pass"


def test_a_name_adds_both_flags_and_a_present_passed_check_passes(tmp_path):
    r, argv, verdict = _run(tmp_path, "external/chat-inference")
    assert r.returncode == 0, r.stderr
    assert argv == _today(tmp_path / "qa-raw.json") + [
        "--external-verdict", "--require-external", "external/chat-inference"]
    assert verdict == "pass"


def test_a_required_check_the_run_did_not_report_blocks(tmp_path):
    r, argv, verdict = _run(tmp_path, "external/chat-inference, contract/identity")
    assert argv[-2:] == ["--require-external", "external/chat-inference, contract/identity"]
    assert r.returncode == 1 and verdict == "block"
    assert "required external checks absent: contract/identity" in r.stdout
