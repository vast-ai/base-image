"""Guard: the promotion Slack headline must describe what HAPPENED.

On 2026-08-14 a QA cell drew a host with no GPU. The required-pass gate did its
job — three GPU tests skipped, `skip != pass`, the cell blocked, and `promote`
was skipped by transitive failure. Nothing was promoted. Slack said:

    Base image promoted — 1 auto tag(s) HELD (QA did not clear them);
    dated tags landed as normal

Every clause of that is false: nothing was promoted, no dated tag landed, and
the "HELD" framing implies the other tags moved. Both arms of the headline
expression opened with "Base image promoted" unconditionally and never consulted
`needs.promote.result`.

That is worse than no notification. Slack is the only place most people look, so
a run where the gate correctly STOPPED a promotion was announced as one where it
let a partial promotion through — inverting the safety signal.
"""
import re

import pytest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
PROMOTE = REPO / ".github" / "workflows" / "promote-base-image.yml"


def _notify_with() -> dict:
    data = yaml.safe_load(PROMOTE.read_text())
    return data["jobs"]["notify"]["with"]


def test_notify_depends_on_promote():
    """The headline cannot consult a job it does not depend on."""
    needs = yaml.safe_load(PROMOTE.read_text())["jobs"]["notify"]["needs"]
    assert "promote" in needs, "notify must depend on promote to report its result"


def test_headline_is_an_ALLOWLIST_on_success():
    """Only a successful promote may open with "promoted".

    The first fix enumerated the bad outcomes ('skipped', 'failure') and let
    everything else fall through to "Base image promoted". A CANCELLED run is
    neither, so on 2026-08-14 Slack announced "Base image promoted — 10 auto
    tag(s) HELD" for a run whose promote job never executed. Enumerating the ways
    a thing can go wrong misses the one nobody listed; requiring the single way it
    goes right cannot be missed.
    """
    headline = " ".join(str(_notify_with()["headline"]).split())
    assert "needs.promote.result != 'success'" in headline, (
        "the headline must key on promote SUCCEEDING, not on a list of known "
        "failure states — an unlisted state falls through to claiming a promotion")


@pytest.mark.parametrize("result", ["skipped", "failure", "cancelled", "some_future_state"])
def test_no_non_success_result_can_claim_a_promotion(result):
    """Evaluate the real expression's leading branch for each result GitHub can
    produce, including one it does not produce yet."""
    headline = " ".join(str(_notify_with()["headline"]).split())
    m = re.search(r"needs\.promote\.result != 'success'\s*&&\s*format\('([^']+)'", headline)
    assert m, "no not-promoted branch keyed on != success"
    msg = m.group(1)
    assert "NOT promoted" in msg
    assert not msg.startswith("Base image promoted"), msg
    assert "{0}" in msg, "the not-promoted headline should name the actual result"


def test_a_successful_promote_can_still_report_holds():
    """The allowlist must not flatten the useful case: a real promotion that held
    some tags still needs to say so."""
    headline = " ".join(str(_notify_with()["headline"]).split())
    assert "auto tag(s) HELD" in headline
    assert "needs.qa-summary.outputs.holds" in headline


def test_status_is_warning_when_promote_did_not_succeed():
    status = str(_notify_with()["status"])
    assert "needs.promote.result != 'success'" in status, (
        "a non-successful promote must not render as a normal green notification")


# ---- the cloudflared contract must actually run at build time --------------

BUILD = REPO / ".github" / "workflows" / "build-base-image.yml"


def _build_jobs():
    return yaml.safe_load(BUILD.read_text())["jobs"]


def test_the_build_runs_the_cloudflared_contract():
    """The Dockerfile fetches cloudflared unpinned, so the contract test IS the
    control. It has to run where the binary changes — on a rebuild — not only on
    a portal-aio/** push, or the unpinned fetch is unguarded between releases."""
    jobs = _build_jobs()
    assert "cloudflared-contract" in jobs, (
        "build-base-image.yml does not run the cloudflared contract test; the "
        "unpinned releases/latest fetch would ship unvalidated")
    run = " ".join(str(s.get("run", "")) for s in jobs["cloudflared-contract"]["steps"])
    assert "test_cloudflared_contract.py" in run


def test_the_contract_job_runs_once_not_per_matrix_cell():
    """Cloudflare rate-limits quick-tunnel creation. A per-cell check (12 configs
    x 5 pythons) would rate-limit itself and then report the rate limit as a
    defect — a gate that fails more the more it runs is one that gets switched
    off."""
    job = _build_jobs()["cloudflared-contract"]
    assert "strategy" not in job, (
        "the contract job must not be a matrix — one run per build, or it "
        "self-inflicts Cloudflare's quick-tunnel rate limit")


def test_the_contract_tests_the_binary_that_SHIPPED():
    """releases/latest can move between the build and the check. Re-downloading
    would test a different binary from the one in the image."""
    run = " ".join(str(s.get("run", ""))
                   for s in _build_jobs()["cloudflared-contract"]["steps"])
    assert "docker cp" in run and "/opt/portal-aio/tunnel_manager/cloudflared" in run, (
        "the job must extract cloudflared from the built image, not re-fetch it")
    assert "CLOUDFLARED_BIN=" in run, "the extracted binary must be handed to the test"
