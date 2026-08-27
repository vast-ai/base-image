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


# --- the throwaway template must be per-CELL, not per-template-file -----------


def test_the_create_step_passes_a_per_cell_name_suffix():
    """Template creation UPSERTS on the NAME, so cells sharing a template FILE share
    one throwaway TEMPLATE — and the first to finish deletes it out from under the
    rest.

    Measured on the SGLang gate: four cells produced two template ids (607556,
    607557); the serverless cu129 cell deleted 607557 at 14:12:51 and the standard
    cu129 cell, still working through offers on that same template, got
    "invalid template hash or id or template not accessible by user" on every attempt
    after that instant. It surfaced as config_error and blocked the gate.

    ADR 0031 decision 3 is what created the collision — "one template, two cells" —
    so the fix belongs here rather than in the templates."""
    body = _step("Create throwaway QA template")["run"]
    assert "--name-suffix" in body, (
        "cells sharing a template file would share one throwaway template id"
    )
    # label alone repeats across CUDA variants and tag alone repeats across the
    # standard/serverless pair; only the two together are unique per cell.
    assert "QA_LABEL" in body and "QA_TAG" in body


def test_the_create_step_exports_what_the_suffix_reads():
    """A suffix built from an unset variable silently degrades to a shared name —
    the exact failure it exists to prevent, back again and harder to see."""
    step = _step("Create throwaway QA template")
    env = step.get("env") or {}
    assert "QA_LABEL" in env and "QA_TAG" in env, (
        f"the suffix reads QA_LABEL/QA_TAG; step env declares {sorted(env)}"
    )


# ---- extra_env must survive its own documentation (2026-08-27) --------------
#
# `extra_env` is a multi-line block that callers use to describe WHY a cell is
# configured the way it is — the serverless cells carry several lines of rationale.
# qa-gate turned every non-empty line into `--env`, and test_template.py rejects any
# `--env` without an `=`:
#
#     Invalid --env format (expected KEY=VAL): # REPORT_ADDR is not product config...
#     {"state": "config_error", "exit_code": 4, "reason": "bad --env format"}
#
# Measured live on the first ComfyUI serverless cell ever run: config_error before a
# GPU was rented, and config_error is deliberately never retried, so the cell simply
# blocked. The same comment block had already been merged into build-sglang.yml and
# build-llama-cpp.yml, where it had not yet bitten only because their runs had been
# dispatched from an earlier commit.
#
# The deeper defect is that two parsers disagreed. The LINTER's reader of the same
# field skips `#` lines (`_serverless_gate_callers`), so a comment there is not only
# harmless but invisible — which is exactly why the author believed it was legal.
# Making the harness match the linter closes the trap; asserting it here keeps them
# matched.

import os
import subprocess


def _extra_env_loop() -> str:
    """The real shell from qa-gate.yml that expands extra_env into --env flags.

    Extracted as a BLOCK, from the `while` to its `done <<< "${EXTRA_ENV}"`, because the
    loop is not one line: reading a single line worked until the loop grew a body and
    would then have silently tested nothing.
    """
    lines = QA_GATE.read_text().splitlines()
    start = next((i for i, l in enumerate(lines)
                  if l.strip().startswith("while IFS= read -r kv")), None)
    assert start is not None, "qa-gate.yml no longer has the extra_env expansion loop"
    end = next((j for j in range(start, len(lines))
                if "EXTRA_ENV" in lines[j] and lines[j].strip().startswith("done")), None)
    assert end is not None, "the extra_env loop no longer terminates on EXTRA_ENV"
    return "\n".join(l.strip() for l in lines[start:end + 1])


def _run_loop(extra_env: str) -> list[str]:
    """Execute the SHIPPED loop, so this tests the harness rather than a copy of it.

    EXTRA_ENV arrives through the ENVIRONMENT, not interpolated into the script — the
    real values are prose containing backticks, quotes and $-expansions, and embedding
    them would make the test fail on its own quoting instead of on the property.
    """
    script = f'ARGS=()\n{_extra_env_loop()}\nprintf "%s\\n" "${{ARGS[@]}}"\n'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         check=True, env={"EXTRA_ENV": extra_env, "PATH": os.environ["PATH"]}).stdout
    return [l for l in out.splitlines() if l != "--env"]


def test_a_comment_in_extra_env_never_reaches_the_client():
    """THE regression. A commented block must yield only the real assignments."""
    got = _run_loop(
        "SERVERLESS=true\n"
        "# REPORT_ADDR is not product configuration and must not be left to the image:\n"
        "   # indented comments too — YAML block scalars keep the indentation\n"
        "REPORT_ADDR=https://qa-no-autoscaler.invalid\n")
    assert got == ["SERVERLESS=true", "REPORT_ADDR=https://qa-no-autoscaler.invalid"], got


def test_every_value_the_loop_emits_is_a_KEY_VAL():
    """The property the client actually enforces, asserted on the harness side."""
    got = _run_loop("A=1\n# note\n\n   \nB=2\n")
    assert all("=" in v for v in got), got
    assert got == ["A=1", "B=2"], got


def test_a_hash_inside_a_VALUE_is_not_treated_as_a_comment():
    """Only a line that STARTS with # is a comment. A `#` inside a value is data —
    dropping those lines would silently delete configuration instead of a remark."""
    got = _run_loop("PROMPT=a#b\nURL=https://x/y#frag\n")
    assert got == ["PROMPT=a#b", "URL=https://x/y#frag"], got


def test_the_shipped_serverless_cells_would_launch():
    """Round-trip over every real caller: run each one's actual extra_env through the
    shipped loop and require the result to be launchable. This is the assertion that
    was red on three workflows at once."""
    bad = {}
    for wf in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        try:
            data = yaml.safe_load(wf.read_text())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for jn, j in (data.get("jobs") or {}).items():
            if not isinstance(j, dict) or not str(j.get("uses", "")).endswith("qa-gate.yml"):
                continue
            env = str((j.get("with") or {}).get("extra_env", "") or "")
            if not env.strip():
                continue
            offenders = [v for v in _run_loop(env) if "=" not in v]
            if offenders:
                bad[f"{wf.name}:{jn}"] = offenders[0][:80]
    assert not bad, f"extra_env lines the client would reject as bad --env: {bad}"
