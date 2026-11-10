"""template-qa.yml: a hand-dispatched, one-off live test of one published template.

The workflow text is read and its run step is executed against a stub launcher, as
test_qa_gate_proofs.py does for qa-gate: what matters is the argv the launcher
receives, that untrusted input can never become shell, and that the instance is
always destroyed while the proof is always summarised and uploaded.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
import yaml

import wfexec

WF = wfexec.REPO / ".github/workflows/template-qa.yml"
HASH = "d91728dcec6db50148dc4333947eb726"


def _wf():
    return yaml.safe_load(WF.read_text())


def _on(wf):
    return wf.get("on", wf.get(True))          # PyYAML reads a bare `on:` key as True


def _steps():
    return {s.get("name"): s for s in _wf()["jobs"]["qa"]["steps"]}


def test_it_is_dispatch_only_with_the_documented_inputs():
    wf = _wf()
    assert wf["name"] == "Template QA"
    on = _on(wf)
    assert list(on) == ["workflow_dispatch"]
    inputs = on["workflow_dispatch"]["inputs"]
    assert list(inputs) == ["template_hash", "capabilities", "gpu", "min_inet",
                            "min_reliability", "max_price", "extra_env", "timeout"]
    assert inputs["template_hash"]["required"] is True and "default" not in inputs["template_hash"]
    assert (inputs["min_inet"]["default"], inputs["min_reliability"]["default"],
            inputs["max_price"]["default"]) == ("2000", "0.99", "2.00")
    assert all(i["type"] == "string" for i in inputs.values())


def test_permissions_concurrency_and_timeout():
    wf = _wf()
    assert wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"] == {"group": "template-qa-${{ inputs.template_hash }}",
                                 "cancel-in-progress": False}
    assert wf["jobs"]["qa"]["timeout-minutes"] == 240


def test_no_run_script_interpolates_an_input_or_a_secret():
    for name, step in _steps().items():
        assert "${{" not in str(step.get("run", "")), f"step {name!r} splices an expression"


def test_the_instance_is_always_destroyed_and_never_kept():
    run = _steps()["Run the template test (DESTROY the instance after)"]["run"]
    assert "--destroy" in run and "--keep" not in run
    assert "--label base-image-qa-template" in run


def test_the_reaper_covers_a_leaked_template_qa_instance():
    reaper = yaml.safe_load((wfexec.REPO / ".github/workflows/qa-reaper.yml").read_text())
    steps = reaper["jobs"][next(iter(reaper["jobs"]))]["steps"]
    sweep = [s for s in steps if s.get("env", {}).get("TEMPLATE_QA_LABEL")]
    assert sweep and sweep[0]["env"]["TEMPLATE_QA_LABEL"] == "base-image-qa-template"
    assert "--image-prefix" not in sweep[0]["run"]


def test_the_proof_is_always_summarised_and_uploaded():
    steps = _steps()
    up = steps["Upload test proof"]
    assert up["if"] == "always()" and up["with"]["path"] == "/tmp/qa-proofs"
    assert up["with"]["retention-days"] == 90
    assert up["with"]["name"] == "${{ steps.proofname.outputs.name }}"
    assert steps["Summarise and verify the test proof"]["if"] == "always()"
    assert steps["Name the test proof artifact"]["if"] == "always()"


# ---------------------------------------------------------------- the run step, executed

def _run_step(tmp_path, launcher_exit=0, **inputs):
    tm = tmp_path / "tm"
    tm.mkdir(exist_ok=True)
    (tm / "test_template.py").write_text(
        "import json, sys, pathlib\n"
        "pathlib.Path(__file__).with_name('argv.json').write_text(json.dumps(sys.argv[1:]))\n"
        f"sys.exit({launcher_exit})\n")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "python").write_text(f'#!/bin/bash\nexec {sys.executable} "$@"\n')
    (bindir / "python").chmod(0o755)
    out = tmp_path / "gh_out"
    out.write_text("")
    env = {"TEMPLATE_HASH": HASH, "CAPABILITIES": "", "GPU": "", "MIN_INET": "2000",
           "MIN_RELIABILITY": "0.99", "MAX_PRICE": "2.00", "EXTRA_ENV": "", "TEST_TIMEOUT": "",
           "HF_TOKEN": "", **inputs}
    script = _steps()["Run the template test (DESTROY the instance after)"]["run"]
    r = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=tmp_path,
                       capture_output=True, text=True,
                       env={"PATH": f"{bindir}:/usr/bin:/bin", "TM": str(tm),
                            "GITHUB_OUTPUT": str(out), **env})
    argv = json.loads((tm / "argv.json").read_text()) if (tm / "argv.json").exists() else None
    return r, argv, out.read_text()


def test_the_default_inputs_build_the_one_command(tmp_path):
    r, argv, out = _run_step(tmp_path)
    assert r.returncode == 0, r.stderr
    assert argv == [HASH, "--destroy", "--label", "base-image-qa-template",
                    "--proof-dir", "/tmp/qa-proofs", "--min-inet", "2000",
                    "--min-reliability", "0.99", "--max-price", "2.00"]
    assert "exit_code=0" in out


def test_every_input_becomes_its_own_argument(tmp_path):
    r, argv, _ = _run_step(tmp_path, CAPABILITIES="chat,embeddings", GPU="RTX 5090",
                           TEST_TIMEOUT="5400", HF_TOKEN="hf_secret",
                           EXTRA_ENV="# a comment\nVLLM_ARGS=--max-model-len 4096\n\nX=a b\n")
    assert r.returncode == 0, r.stderr
    assert argv[argv.index("--gpu") + 1] == "RTX 5090"
    assert argv[argv.index("--capabilities") + 1] == "chat,embeddings"
    assert argv[argv.index("--timeout") + 1] == "5400"
    envs = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
    assert envs == ["VLLM_ARGS=--max-model-len 4096", "X=a b", "HF_TOKEN=hf_secret"]
    assert "--keep" not in argv


def test_a_hostile_value_stays_data(tmp_path):
    """A value written to break out of the shell is passed through as one argument."""
    r, argv, _ = _run_step(tmp_path, EXTRA_ENV='X="; echo pwned; #\nY=$(echo pwned)')
    assert r.returncode == 0, r.stderr
    assert 'X="; echo pwned; #' in argv and "Y=$(echo pwned)" in argv
    assert "pwned" not in r.stdout and "pwned" not in r.stderr


@pytest.mark.parametrize("field,value,why", [
    ("TEMPLATE_HASH", "not-a-hash", "template_hash must be 32"),
    ("TEMPLATE_HASH", HASH + '"; echo pwned', "template_hash must be 32"),
    ("TEMPLATE_HASH", HASH.upper(), "template_hash must be 32"),
    ("GPU", '"; echo pwned', "gpu must be"),
    ("CAPABILITIES", "chat;rm -rf /", "capabilities must be"),
    ("MIN_INET", "lots", "min_inet must be"),
    ("MIN_RELIABILITY", "1.5", "min_reliability must be"),
    ("MAX_PRICE", "$(echo 9)", "max_price must be"),
    ("TEST_TIMEOUT", "1h", "timeout must be"),
    ("EXTRA_ENV", "not an assignment", "extra_env line is not KEY=VALUE"),
])
def test_a_bad_input_fails_early_and_the_launcher_never_runs(tmp_path, field, value, why):
    r, argv, _ = _run_step(tmp_path, **{field: value})
    assert r.returncode == 2 and argv is None
    assert f"::error::{why}" in r.stdout
    assert "pwned" not in re.sub(r"::error::.*", "", r.stdout + r.stderr)


def test_the_job_fails_when_the_launcher_fails(tmp_path):
    r, argv, out = _run_step(tmp_path, launcher_exit=1)
    assert r.returncode == 1 and argv is not None and "exit_code=1" in out


def test_the_summary_names_the_template_and_the_artifact_first(tmp_path):
    import proof as P
    from test_template import redact_ips
    rec = P.ProofRecorder(str(tmp_path / "qa-proofs"), [HASH], redact_ips, env={},
                          git_state=lambda p: ("x", False))
    rec.begin(HASH, {"name": "vLLM Qwen"}, "vastai/vllm:v1", [], {})
    rec.set_outcome("passed", 0)
    rec.write()
    script = _steps()["Summarise and verify the test proof"]["run"].replace(
        "/tmp/qa-proofs", str(tmp_path / "qa-proofs"))
    summary = tmp_path / "summary.md"
    summary.write_text("")
    r = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=tmp_path, capture_output=True,
                       text=True, env={"PATH": f"{sys.executable.rsplit('/', 1)[0]}:/usr/bin:/bin",
                                       "TM": str(wfexec.REPO / "tools/template_manager"),
                                       "TEMPLATE_HASH": HASH, "ARTIFACT": "template-qa-proof-x",
                                       "GITHUB_STEP_SUMMARY": str(summary)})
    assert r.returncode == 0, r.stderr
    text = summary.read_text()
    assert text.startswith(f"## Template QA — `{HASH}`")
    assert "Proof artifact: `template-qa-proof-x`" in text
    assert "**passed**" in text and "vLLM Qwen" in text
