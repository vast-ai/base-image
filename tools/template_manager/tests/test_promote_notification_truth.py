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


def test_headline_consults_the_promote_result():
    headline = str(_notify_with()["headline"])
    assert "needs.promote.result" in headline, (
        "the headline must branch on whether promote actually ran — without it, a "
        "skipped promotion is announced as a completed one"
    )


def test_a_skipped_promotion_is_not_announced_as_promoted():
    """The specific regression: skipped must produce a NOT-promoted headline."""
    headline = str(_notify_with()["headline"])
    m = re.search(r"needs\.promote\.result == 'skipped' && '([^']+)'", headline)
    assert m, "no explicit branch for a skipped promote"
    msg = m.group(1)
    assert "NOT promoted" in msg, f"skipped-promote headline must say so, got: {msg}"
    assert not re.match(r"^Base image promoted", msg), (
        f"skipped-promote headline still opens by claiming a promotion: {msg}")


def test_status_is_warning_when_promote_did_not_succeed():
    status = str(_notify_with()["status"])
    assert "needs.promote.result != 'success'" in status, (
        "a non-successful promote must not render as a normal green notification")
