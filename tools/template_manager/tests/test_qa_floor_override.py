"""ADR 0047: a dispatch may narrow an engine's QA hardware only for a custom tag,
only on compute_cap, with a validated price ceiling, and visibly.

The validation lives in build-vllm.yml's preflight step, and the QA cells read its
OUTPUTS rather than the raw dispatch inputs (lint rule L099 holds the wiring). These
tests EXECUTE that step's script, as CI runs it, rather than searching it for text: a
guard that only has to be present can be present and wrong.

Also pinned here: qa-gate.yml refuses a non-positive max_price before renting, and the
client refuses one at the door, because `--max-price 0` used to remove the cap.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wfexec  # noqa: E402

REPO = wfexec.REPO
BUILD_VLLM = REPO / ".github" / "workflows" / "build-vllm.yml"
QA_GATE = wfexec.QA_GATE
HARD_MAX = "15.00"


def _override(tmp_path, filters="", price="", tag=""):
    script = wfexec.step_script(BUILD_VLLM, "preflight", "Validate QA selection overrides")
    r = wfexec.run_step(script, tmp_path, {}, {
        "QA_SET_FILTERS": filters, "QA_MAX_PRICE": price, "CUSTOM_IMAGE_TAG": tag,
        "QA_MAX_PRICE_HARD_MAX": HARD_MAX})
    r["outputs"] = dict(l.split("=", 1) for l in r["github_output"].splitlines() if "=" in l)
    return r


def test_the_hard_max_the_tests_use_is_the_workflows():
    env = yaml.safe_load(BUILD_VLLM.read_text())["env"]
    assert env["QA_MAX_PRICE_HARD_MAX"] == HARD_MAX


def test_an_empty_dispatch_changes_nothing(tmp_path):
    """Every scheduled run and every dispatch that sets neither input: no filters,
    qa-gate's own default ceiling, and no note, so the headline renders as before."""
    r = _override(tmp_path)
    assert r["code"] == 0, r["err"]
    assert r["outputs"] == {"set-filters": "", "max-price": "2.00", "note": ""}
    assert r["summary"] == ""


@pytest.mark.parametrize("filters,price", [("compute_cap.gte=1000", ""), ("", "5.00")])
def test_an_override_without_a_custom_tag_is_refused(tmp_path, filters, price):
    """Condition 1. A version or nightly tag publishes under the sm_80 production
    floor, so certifying it on narrowed hardware tests less than production
    promises. The price alone counts too: it is still an override."""
    r = _override(tmp_path, filters, price, tag="")
    assert r["code"] != 0
    assert "CUSTOM_IMAGE_TAG" in r["out"] + r["err"]
    assert "set-filters=" not in r["github_output"]


@pytest.mark.parametrize("filters", [
    "gpu_ram.gte=81920",                      # another key
    "machine_id.gte=123 machine_id.lte=123",  # pins the gate to one host
    "compute_cap.eq=1000",                    # another operator
    "compute_cap.gte=abc",                    # not a number
    "compute_cap.gte=1000 *",                 # a glob must be refused, not expanded
])
def test_anything_but_a_compute_cap_bound_is_refused(tmp_path, filters):
    """Condition 2. The raise-only merge in create.py accepts a NEW key or operator
    unchecked, so the allowlist has to live here."""
    (tmp_path / "decoy.txt").write_text("")   # would be what `*` expands to
    r = _override(tmp_path, filters, tag="hy4-preview")
    assert r["code"] != 0, r["out"]
    assert "refused" in r["out"] + r["err"]


@pytest.mark.parametrize("price", ["0", "0.00", "-1", "abc", "$12", "15.01", "120", "1e3"])
def test_a_price_outside_the_bound_is_refused(tmp_path, price):
    """Condition 3. `0` used to remove the cap, a malformed value used to exit 2 and
    be read as "no offers", and nothing stopped a typo renting at $120/hr."""
    r = _override(tmp_path, "compute_cap.gte=1000", price, tag="hy4-preview")
    assert r["code"] != 0, r["out"]
    assert "QA_MAX_PRICE" in r["out"] + r["err"]


def test_a_valid_bracket_passes_and_says_so(tmp_path):
    """Condition 2 and 4: the range form is accepted, and the run names what it
    certified on, in the summary and in the note the Slack headline carries."""
    r = _override(tmp_path, "compute_cap.gte=1000  compute_cap.lte=1030", "12.00",
                  tag="hy4-preview")
    assert r["code"] == 0, r["err"]
    assert r["outputs"]["set-filters"] == "compute_cap.gte=1000 compute_cap.lte=1030"
    assert r["outputs"]["max-price"] == "12.00"
    note = r["outputs"]["note"]
    assert "compute_cap.gte=1000 compute_cap.lte=1030" in note and "$12.00/hr" in note
    assert "hy4-preview" in r["summary"] and "NOT on the template's floors" in r["summary"]


def test_the_hard_max_itself_is_allowed(tmp_path):
    r = _override(tmp_path, "", HARD_MAX, tag="hy4-preview")
    assert r["code"] == 0, r["err"]
    assert r["outputs"]["max-price"] == HARD_MAX
    assert "template floors" in r["outputs"]["note"]


def test_the_headline_carries_the_override_note():
    """Condition 4 at the reader: prefixed, so a clamped headline keeps it."""
    notify = yaml.safe_load(BUILD_VLLM.read_text())["jobs"]["notify"]["with"]["headline"]
    assert notify.startswith("${{ format('{0}{1}', needs.preflight.outputs.qa-override-note,")


def test_both_cells_read_the_validated_outputs():
    """The same property L099 lints for, pinned where the workflow is exercised."""
    jobs = yaml.safe_load(BUILD_VLLM.read_text())["jobs"]
    for cell in ("qa", "qa-serverless"):
        w = jobs[cell]["with"]
        assert w["set_filters"] == "${{ needs.preflight.outputs.qa-set-filters }}", cell
        assert w["max_price"] == "${{ needs.preflight.outputs.qa-max-price }}", cell


# ---- qa-gate.yml: the reusable gate refuses a bad ceiling for EVERY caller -------

_CREATE_BIND = {"inputs.repo": "vllm", "inputs.tag": "t-cuda-13.0-amd64",
                "github.workspace": "/ws", "inputs.template_dir": "external/x"}


def _create(tmp_path, price):
    wf = yaml.safe_load(QA_GATE.read_text())
    step = [s for s in wf["jobs"]["qa"]["steps"] if s.get("id") == "create"][0]
    script = step["run"]
    for expr, val in _CREATE_BIND.items():
        script = script.replace("${{ " + expr + " }}", val)
    assert "${{" not in script, "unresolved expression — bind it, never guess"
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    calls = tmp_path / "python.calls"
    (bindir / "python").write_text(f'#!/bin/bash\necho "$@" >> {calls}\ncat >/dev/null\nexit 0\n')
    (bindir / "python").chmod(0o755)
    out = tmp_path / "gh_out"
    out.write_text("")
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "QA_MAX_PRICE": price, "SET_FILTERS": "",
           "STAGING_NS": "ns", "QA_LABEL": "l", "QA_TAG": "t", "TM": "tm",
           "GITHUB_OUTPUT": str(out)}
    r = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True)
    return r, calls.read_text() if calls.exists() else ""


@pytest.mark.parametrize("price", ["0", "-1", "abc", ""])
def test_qa_gate_refuses_a_non_positive_price_before_renting(tmp_path, price):
    r, calls = _create(tmp_path, price)
    assert r.returncode != 0
    assert "refusing to rent" in r.stdout + r.stderr
    assert calls == "", "a refused price still reached create.py"


def test_qa_gate_passes_a_valid_price_through(tmp_path):
    r, calls = _create(tmp_path, "2.00")
    assert r.returncode == 0, r.stderr
    assert "create.py" in calls


def test_qa_gate_never_splices_max_price_into_bash():
    """A caller may now feed max_price from a dispatch input, so it reaches the run
    script through env:, never as a `${{ }}` pasted in before bash parses it."""
    steps = yaml.safe_load(QA_GATE.read_text())["jobs"]["qa"]["steps"]
    for s in steps:
        assert "inputs.max_price" not in (s.get("run") or ""), s.get("name")
    test = [s for s in steps if s.get("id") == "test"][0]
    assert test["env"]["QA_MAX_PRICE"] == "${{ inputs.max_price }}"
    assert re.search(r'--max-price "\$\{QA_MAX_PRICE\}"', test["run"])


# ---- the client: a price that removes the cap is a usage error ------------------

sys.path.insert(0, str(REPO / "tools" / "template_manager"))


@pytest.mark.parametrize("value", ["0", "-2", "nan", "inf", "abc"])
def test_the_client_refuses_a_price_that_removes_the_cap(value):
    import test_template as tt
    with pytest.raises(argparse.ArgumentTypeError):
        tt._positive_price(value)


def test_the_client_accepts_a_real_price():
    import test_template as tt
    assert tt._positive_price("2.00") == 2.0
