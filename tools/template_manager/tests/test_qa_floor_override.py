"""ADR 0047: a dispatch may narrow an image's QA hardware only for a custom tag,
only on compute_cap, with a validated price ceiling, and visibly. It is the accepted
pattern for every build workflow with a QA cell and a CUSTOM_IMAGE_TAG input.

The rules live in ONE place, .github/actions/validate-qa-override, and each such
workflow's QA cells read that action's OUTPUTS rather than the raw dispatch inputs
(lint rules L099 and L100 hold the wiring). These tests EXECUTE the action's script as
CI runs it, rather than searching it for text: a guard that only has to be present can
be present and wrong.

Also pinned here: qa-gate.yml refuses a non-positive max_price before renting, the
client refuses one at the door (`--max-price 0` used to remove the cap), and
notify-slack puts the override in the header whether the caller sets a headline or not.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wfexec  # noqa: E402

REPO = wfexec.REPO
WORKFLOWS = REPO / ".github" / "workflows"
ACTION = REPO / ".github" / "actions" / "validate-qa-override" / "action.yml"
NOTIFY = WORKFLOWS / "notify-slack.yml"
QA_GATE = wfexec.QA_GATE


def _action_step():
    return yaml.safe_load(ACTION.read_text())["runs"]["steps"][0]


HARD_MAX = _action_step()["env"]["QA_MAX_PRICE_HARD_MAX"]


def _override(tmp_path, filters="", price="", tag=""):
    script = _action_step()["run"]
    assert "${{" not in script, "the action's script must take its inputs via env:"
    r = wfexec.run_step(script, tmp_path, {}, {
        "QA_SET_FILTERS": filters, "QA_MAX_PRICE": price, "CUSTOM_IMAGE_TAG": tag,
        "QA_MAX_PRICE_HARD_MAX": HARD_MAX})
    r["outputs"] = dict(l.split("=", 1) for l in r["github_output"].splitlines() if "=" in l)
    return r


def _custom_tag_qa_workflows():
    """Every workflow L100 scopes: a CUSTOM_IMAGE_TAG dispatch input and a QA cell."""
    out = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        d = yaml.safe_load(wf.read_text())
        on = d.get("on", d.get(True)) or {}
        disp = on.get("workflow_dispatch") if isinstance(on, dict) else None
        if "CUSTOM_IMAGE_TAG" in ((disp or {}).get("inputs") or {}) and any(
                "qa-gate.yml" in str(v.get("uses", "")) for v in d["jobs"].values()):
            out.append(wf.name)
    return out


def test_the_hard_max_is_the_documented_one():
    assert HARD_MAX == "15.00"


def test_every_image_with_a_custom_tag_qa_phase_is_in_scope():
    """The accepted pattern covers them all, and the promotion gates (mainline tags
    only, where condition 1 refuses every override) are deliberately not among them."""
    scoped = _custom_tag_qa_workflows()
    assert len(scoped) >= 8, scoped
    assert "promote-base-image.yml" not in scoped and "promote-pytorch.yml" not in scoped


def test_an_empty_dispatch_changes_nothing(tmp_path):
    """Every scheduled run and every dispatch that sets neither input: no filters,
    qa-gate's own default ceiling, and no note, so the header renders as before."""
    r = _override(tmp_path)
    assert r["code"] == 0, r["err"]
    assert r["outputs"] == {"set-filters": "", "max-price": "2.00", "note": ""}
    assert r["summary"] == ""


@pytest.mark.parametrize("filters,price", [("compute_cap.gte=1000", ""), ("", "5.00")])
def test_an_override_without_a_custom_tag_is_refused(tmp_path, filters, price):
    """Condition 1. A version or nightly tag publishes under the production floor, so
    certifying it on narrowed hardware tests less than production promises. The price
    alone counts too: it is still an override."""
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
    """Conditions 2 and 4: the range form is accepted, and the run names what it
    certified on, in the summary and in the note the Slack header carries."""
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


@pytest.mark.parametrize("wf", _custom_tag_qa_workflows())
def test_every_cell_reads_the_validated_outputs(wf):
    """The same property L100 lints for, pinned per workflow where it is exercised:
    the action runs in one job, every QA cell takes that job's outputs, and the
    notifier passes its note."""
    jobs = yaml.safe_load((WORKFLOWS / wf).read_text())["jobs"]
    gate = [j for j, v in jobs.items() if any(
        st.get("uses") == "./.github/actions/validate-qa-override"
        for st in (v.get("steps") or []))]
    assert len(gate) == 1, gate
    g = gate[0]
    for cell, v in jobs.items():
        if "qa-gate.yml" in str(v.get("uses", "")):
            assert f"needs.{g}.outputs.qa-set-filters" in v["with"]["set_filters"], cell
            assert v["with"]["max_price"] == f"${{{{ needs.{g}.outputs.qa-max-price }}}}", cell
        if "notify-slack.yml" in str(v.get("uses", "")):
            assert v["with"]["qa-override-note"] == f"${{{{ needs.{g}.outputs.qa-override-note }}}}"


def _notify_header(tmp_path, headline, note):
    # The notify harness test_qa_gate_executed.py already trusts: bash -e, a stubbed
    # curl that captures the --data payload instead of posting it.
    from test_qa_gate_executed import _run
    script = wfexec.step_script(NOTIFY, "notify", "Notify Slack")
    body = tmp_path / "body.json"
    env = {"HEADLINE": headline, "BUILD_RESULT": "success", "STATUS": "",
           "IMAGE_NAME": "vLLM", "IMAGE_TAGS": "[]", "TRIGGER": "workflow_dispatch",
           "IMAGE_REF": "x", "RUN_URL": "https://example/run/1", "CURL_BODY": str(body),
           "SLACK_WEBHOOK_URL": "https://hooks.example/x", "QA_OVERRIDE_NOTE": note}
    r, _, _ = _run(script, tmp_path, env, stub_curl=True)
    assert r.returncode == 0, r.stderr
    payload = json.loads(body.read_text())
    return next(b["text"]["text"] for b in payload["attachments"][0]["blocks"]
                if b.get("type") == "header")


NOTE = "[QA override: compute_cap.gte=1000 compute_cap.lte=1030, max $12.00/hr] "


@pytest.mark.parametrize("headline", ["vLLM promoted — live-GPU QA passed", ""])
def test_the_header_names_the_override(tmp_path, headline):
    """Condition 4 at the reader. Prefixed to the caller's headline AND to the default
    one: unsloth-studio sets no headline, and a narrowed pass must still say so."""
    h = _notify_header(tmp_path, headline, NOTE)
    assert "QA override: compute_cap.gte=1000 compute_cap.lte=1030" in h, h


def test_no_note_leaves_the_header_as_it_was(tmp_path):
    assert _notify_header(tmp_path, "vLLM promoted — live-GPU QA passed", "") \
        == "✅ vLLM promoted — live-GPU QA passed"


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
