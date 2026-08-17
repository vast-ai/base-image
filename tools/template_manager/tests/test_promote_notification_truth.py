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


def test_the_contract_fans_out_over_ARCHITECTURE_ONLY():
    """Two competing constraints, and the matrix has to satisfy both.

    Dockerfile fetches `cloudflared-linux-${TARGETARCH}`, so amd64 and arm64 are
    different release artifacts — testing one says nothing about the other, and a
    release where arm64 lags would ship unguarded. But Cloudflare rate-limits
    quick-tunnel creation, so a cell per config x python (12 x 5) would
    rate-limit itself and then report the rate limit as a defect.

    The matrix must therefore be over arch and nothing else.
    """
    job = _build_jobs()["cloudflared-contract"]
    matrix = (job.get("strategy") or {}).get("matrix")
    assert matrix, "the contract job must fan out over architecture"
    assert set(matrix) == {"arch"}, (
        f"the contract matrix must be architecture ONLY, got dimensions {sorted(matrix)} "
        "— any other dimension multiplies quick-tunnel creations into Cloudflare's "
        "rate limiter")
    assert set(matrix["arch"]) == {"amd64", "arm64"}, (
        f"both shipped architectures must be covered, got {matrix['arch']}")
    assert len(matrix["arch"]) <= 2


def test_the_arm64_cell_can_actually_execute_its_binary():
    """An arm64 ELF does not run on an amd64 runner without binfmt. Without QEMU
    the cell would fail on exec and read as a contract break."""
    steps = _build_jobs()["cloudflared-contract"]["steps"]
    qemu = [s for s in steps if "setup-qemu" in str(s.get("uses", ""))]
    assert qemu, "the arm64 cell has no QEMU/binfmt setup; it cannot exec the binary"
    assert "arm64" in str(qemu[0].get("if", "")), (
        "QEMU should be conditional on the arm64 cell")
    extract = " ".join(str(s.get("run", "")) for s in steps)
    assert "--platform linux/${{ matrix.arch }}" in extract, (
        "the extract must pull the per-arch binary, not always amd64")


def test_the_contract_result_reaches_the_notification():
    """A `needs:` edge is not a control by itself. This rendered green from
    needs.build.result alone, so a failing contract was invisible: the staging
    tags are pushed upstream by `build` and promote is a separate dispatch."""
    notify = _build_jobs()["notify"]
    assert "cloudflared-contract" in notify["needs"]
    rendered = " ".join(str(v) for v in notify["with"].values())
    assert "needs.cloudflared-contract.result" in rendered, (
        "the contract result must reach the notification's rendered status, not "
        "merely appear in its needs")


def test_the_extract_is_filter_aware():
    """build-base-image.yml takes a FILTER input. Reading configs[0] would look
    for a tag a FILTERed dispatch never built and go red for a non-contract
    reason; the merge matrix is what this run actually produced."""
    steps = _build_jobs()["cloudflared-contract"]["steps"]
    run = " ".join(str(s.get("run", "")) for s in steps)
    # Assert on the FILE, not on the substring "configs[0]" — the step's own
    # comment explains why it does not read configs[0], so a substring check
    # matches the prose that documents the fix. (Same shape as a linter rule
    # firing on the docstring describing it.)
    assert "configs/base-image.json" not in run, (
        "the extract reads the static config table; a FILTERed dispatch would "
        "send it looking for a tag that was never built")
    assert "MERGE_MATRIX" in run, (
        "the extract must derive its tag from the run's merge matrix — what this "
        "run actually produced")


def test_the_contract_tests_the_binary_that_SHIPPED():
    """releases/latest can move between the build and the check. Re-downloading
    would test a different binary from the one in the image."""
    run = " ".join(str(s.get("run", ""))
                   for s in _build_jobs()["cloudflared-contract"]["steps"])
    assert "docker cp" in run and "/opt/portal-aio/tunnel_manager/cloudflared" in run, (
        "the job must extract cloudflared from the built image, not re-fetch it")
    assert "CLOUDFLARED_BIN=" in run, "the extracted binary must be handed to the test"
