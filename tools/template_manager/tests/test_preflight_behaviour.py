"""Preflight fails closed — executed, not grepped (ADR 0019 cond 3).

The previous tests here were:

    block = raw.split("\\n  preflight:", 1)[1].split("\\n  resolve-digests:", 1)[0]
    assert "VAST_API_KEY" in block and "exit 1" in block

Both strings occur elsewhere in the same block (`VAST_API_KEY` in the `env:`
mapping, `exit 1` in the unrelated missing-secrets branch), so deleting both
guards outright left the assertion green. The step is a self-contained script
whose every input already arrives via `env:` — which makes running it the
obvious move, and asserting its exit code the honest one.
"""

from __future__ import annotations

import pytest

from wfexec import PROMOTE, run_step, step_script

STEP = "Check the gate can actually run"

BASE = {
    "NS_STAGING": "staging",
    "NS_PROD": "prod",
    "DH_USER": "user",
    "DH_TOKEN": "token",
    "STAGING_DATE": "2026-08-07",
    "VAST_API_KEY": "key",
}


def _run(tmp_path, **over):
    env = {**BASE, **over}
    return run_step(step_script(PROMOTE, "preflight", STEP), tmp_path / "pf", {}, env)


def test_the_happy_path_proceeds(tmp_path):
    """A guard suite that only tests failures can be satisfied by `exit 1`."""
    r = _run(tmp_path)
    assert r["code"] == 0, r["err"]
    assert "QA gate ready" in r["out"]


def test_a_missing_qa_key_fails_before_approval_is_requested(tmp_path):
    """Without the key every QA cell would skip and report green — the exact
    skip-as-pass shape the gate exists to close, one layer up."""
    assert _run(tmp_path, VAST_API_KEY="")["code"] == 1


@pytest.mark.parametrize("secret", ["NS_STAGING", "NS_PROD", "DH_USER", "DH_TOKEN"])
def test_every_dockerhub_secret_is_checked(tmp_path, secret):
    """The header comment and the ADR both say 'all DockerHub secrets'. Two of
    the four were unchecked, so the failure surfaced a job later."""
    r = _run(tmp_path, **{secret: ""})
    assert r["code"] == 1, f"{secret} missing did not fail preflight"


@pytest.mark.parametrize("bad", ["2026-8-7", "yesterday", "", "2026-08-07 ",
                                 "$(touch pwned)"])
def test_a_malformed_staging_date_is_refused(tmp_path, bad):
    """STAGING_DATE reaches ~70 registry refs and, before this, the promote job's
    shell. Pin its shape where checking is free."""
    assert _run(tmp_path, STAGING_DATE=bad)["code"] == 1


def test_a_staging_date_cannot_execute(tmp_path):
    canary = tmp_path / "pwned"
    _run(tmp_path, STAGING_DATE=f'2026-08-07"; touch {canary}; #')
    assert not canary.exists(), "the dispatch input executed as shell"


def test_there_is_no_way_to_proceed_without_the_qa_key(tmp_path):
    """The SKIP_QA bypass was removed (ADR 0019, amended 2026-08-07). No env
    combination may reach 'QA gate ready' with an empty key — if one did, the
    bypass would be back without anyone deciding to bring it back."""
    for extra in ({}, {"SKIP_QA": "true"}, {"SKIP_QA": "true", "SKIP_REASON": "x"},
                  {"SKIP_REASON": "x"}, {"FORCE": "true"}):
        r = _run(tmp_path, VAST_API_KEY="", **extra)
        assert r["code"] == 1, f"preflight passed with no key given {extra}"
        assert "QA gate ready" not in r["out"]


def test_the_error_names_the_sanctioned_alternative(tmp_path):
    """A gate with no stated alternative is a gate people route around. Point at
    the workflow that exists for it rather than leaving the operator to invent
    something with crane."""
    r = _run(tmp_path, VAST_API_KEY="")
    assert "Move Base Auto Tag" in r["out"] + r["err"]
