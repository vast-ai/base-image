"""Guard: qa-gate.yml's ADR 0019 inputs must default to today's behaviour.

The gate is shared by three consumers with different safety postures. Base needs
fail-closed-on-missing-key, required-test assertions and bounded retries; comfyui
and vllm are mid-rollout and must keep the advisory ramp they were validated on.
That only works while every new input's default reproduces the old behaviour — a
default flipped "to be safe" would change two live gates without touching their
callers, which is exactly the kind of change nobody would look for.

These also pin the two properties that make the gate mean anything: retries fire
on the infra-inconclusive outcome ONLY, and the verdict is delegated to the
tested Python rather than re-implemented in bash.
"""
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
QA_GATE = REPO / ".github" / "workflows" / "qa-gate.yml"


def _wf():
    data = yaml.safe_load(QA_GATE.read_text())
    # YAML 1.1 resolves a bare ``on:`` key to the boolean True.
    return data, data.get("on", data.get(True))["workflow_call"]["inputs"]


def _qa_steps():
    data, _ = _wf()
    return data["jobs"]["qa"]["steps"]


def _step(name_fragment):
    for s in _qa_steps():
        if name_fragment in (s.get("name") or "") or name_fragment in (s.get("uses") or ""):
            return s
    raise AssertionError(f"no step matching {name_fragment!r}")


# --- defaults must not disturb the existing consumers ----------------------

@pytest.mark.parametrize("name,default", [
    ("require_key", False),      # comfyui/vllm keep the advisory ramp
    ("require_tests", ""),       # no assertion unless a caller asks for one
    ("retries", 0),              # single attempt, as before
    ("evidence_name", ""),       # no artifact upload unless named
])
def test_new_input_defaults_preserve_current_behaviour(name, default):
    _, inputs = _wf()
    assert name in inputs, f"{name} input is gone — a caller passing it would fail"
    assert inputs[name]["default"] == default, (
        f"{name} default changed to {inputs[name]['default']!r}; this silently alters "
        f"the comfyui and vllm gates without touching their callers"
    )


def test_require_floor_still_defaults_true():
    """Pre-existing guarantee (ADR 0005) — the additions must not disturb it."""
    _, inputs = _wf()
    assert inputs["require_floor"]["default"] is True


def test_secrets_stay_optional():
    """A required: true secret would break any caller that does not pass it."""
    data, _ = _wf()
    on = data.get("on", data.get(True))
    for name, spec in on["workflow_call"]["secrets"].items():
        assert spec.get("required") is not True, f"secret {name} became required"


# --- fail-closed -----------------------------------------------------------

def test_missing_key_fails_when_require_key_is_set():
    body = _step("Gate readiness")["run"]
    assert "inputs.require_key" in body, "require_key is not consulted in the readiness guard"
    assert "exit 1" in body, "require_key does not fail the job — it would skip and report green"


# --- retries fire only where they are honest -------------------------------

def test_retry_loop_only_retries_the_infra_outcome():
    """Exit 2 (no_offers) means the image was never tested. Retrying 1 (a real
    failure), 5 (instance crash) or 4 (config error) would launder a red."""
    body = _step("Run live test")["run"]
    assert 'CODE" -ne 2' in body, (
        "the retry loop no longer breaks on every non-2 exit code — it may now be "
        "retrying real failures"
    )
    assert "inputs.retries" in body and "inputs.retry_delay" in body


# --- the verdict stays delegated -------------------------------------------

def test_verdict_is_delegated_to_the_tested_module():
    body = _step("Verdict")["run"]
    assert "qa_verdict.py" in body, "verdict logic drifted back into inline bash"
    assert "--require-tests" in body, "required-tests assertion is not wired through"


def test_verdict_handles_all_three_outcomes_and_defaults_to_blocking():
    body = _step("Verdict")["run"]
    for outcome in ("pass)", "block)", "inconclusive)"):
        assert outcome in body, f"verdict case {outcome} is unhandled"
    assert "*)" in body, "no catch-all — an unrecognised verdict would fall through as success"


def test_only_a_scheduled_run_may_soft_pass():
    """ADR 0005: a gating path holds so a human looks; only the unattended
    schedule soft-passes, and never silently."""
    body = _step("Verdict")["run"]
    assert "github.event_name" in body and "schedule" in body
    assert "soft_pass=true" in body


# --- evidence --------------------------------------------------------------

def test_evidence_upload_runs_even_when_the_cell_blocked():
    """The BLOCKed cell's payload is the one a reviewer most needs."""
    step = _step("Upload verdict evidence")
    assert "always()" in step["if"]
    assert step["with"]["name"] == "${{ inputs.evidence_name }}", (
        "artifact name must come from the caller — a fixed name collides across "
        "matrix cells and upload-artifact@v4 rejects duplicates"
    )


# --- adding an input must not change a caller that ignores it ---------------
#
# This replaces a test that asserted NO existing caller passed any of the ADR 0019
# inputs. That was the right guard for its moment: it made "the defaults preserve
# behaviour" a complete argument rather than a partial one, and its failure message
# said to re-validate the gate before relying on that argument. ADR 0031 is that
# re-validation — build-vllm.yml and build-comfyui.yml now pass `require_tests` and
# `retries` deliberately, proven on live cells.
#
# The caller-side assertion had to go because it decayed by design: every legitimate
# opt-in broke it, so it would have been deleted eventually with no replacement. The
# invariant underneath it does not decay, and is checked directly instead — an input
# added to qa-gate.yml defaults to the behaviour that existed before it.

BEHAVIOUR_PRESERVING_DEFAULTS = {
    "require_key": False,      # advisory-skip on a missing key, as before ADR 0019
    "require_tests": "",       # no name required to have passed
    "retries": 0,              # one attempt
    "evidence_name": "",       # no artifact uploaded
}


@pytest.mark.parametrize("name,expected", sorted(BEHAVIOUR_PRESERVING_DEFAULTS.items()))
def test_new_inputs_default_to_prior_behaviour(name, expected):
    """A caller that ignores an input must behave exactly as it did before the input
    existed. This is what lets qa-gate.yml grow without re-validating every gate."""
    data = yaml.safe_load((REPO / ".github/workflows/qa-gate.yml").read_text())
    # PyYAML resolves the bare `on:` key to the boolean True.
    trigger = data[True] if True in data else data["on"]
    inputs = trigger["workflow_call"]["inputs"]
    assert name in inputs, f"{name} is gone — a caller relying on its default now behaves differently"
    assert inputs[name].get("default") == expected, (
        f"{name} defaults to {inputs[name].get('default')!r}, not {expected!r} — changing a "
        "default silently changes every caller that does not pass it"
    )


@pytest.mark.parametrize("caller", ["build-vllm.yml", "build-comfyui.yml"])
def test_a_caller_that_opts_into_require_tests_names_something(caller):
    """Opting in with an empty value is worse than not opting in: it reads as a gate
    in the diff and asserts nothing at runtime. The same trap L057 closes on the
    template side, checked here on the caller side."""
    data = yaml.safe_load((REPO / ".github/workflows" / caller).read_text())
    seen = False
    for job in data["jobs"].values():
        if "qa-gate.yml" not in (job.get("uses") or ""):
            continue
        with_ = job.get("with") or {}
        if "require_tests" not in with_:
            continue
        seen = True
        assert str(with_["require_tests"]).split(), (
            f"{caller} passes an empty require_tests — that is a gate in name only"
        )
    assert seen, f"{caller} no longer passes require_tests — its gate lost its fail-not-skip half"
