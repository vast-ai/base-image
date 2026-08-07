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
    "SKIP_QA": "false",
    "SKIP_REASON": "",
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


def test_skip_qa_without_a_reason_is_refused(tmp_path):
    """An unexplained bypass is not auditable, and the run history is what ADR
    cond 3 relies on as the tripwire's record."""
    assert _run(tmp_path, SKIP_QA="true", SKIP_REASON="")["code"] == 1


def test_skip_qa_with_a_reason_proceeds_without_the_key(tmp_path):
    """The bypass is meant to work when the gate cannot run at all — otherwise a
    broken key would leave no path forward and the flag would get deleted."""
    r = _run(tmp_path, SKIP_QA="true", SKIP_REASON="verified by hand", VAST_API_KEY="")
    assert r["code"] == 0, r["err"]
    assert "verified by hand" in r["out"]


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


def test_skip_qa_is_checked_before_the_key_so_the_bypass_is_reachable(tmp_path):
    """Ordering matters: if the key check ran first, SKIP_QA could never be used
    in the one situation it is for."""
    r = _run(tmp_path, SKIP_QA="true", SKIP_REASON="key rotated", VAST_API_KEY="")
    assert r["code"] == 0
