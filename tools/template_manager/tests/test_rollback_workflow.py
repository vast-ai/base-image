"""The rollback path's safety properties (ADR 0019 — the gate's escape hatch).

A rollback workflow is the thing you reach for at 3am with an incident open. Its
failure mode is not "doesn't work" — you find that out immediately — it is "works,
and quietly does the wrong thing". Each test below pins one way that could happen.
"""
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
ROLLBACK = REPO / ".github/workflows/rollback-base-auto.yml"


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(ROLLBACK.read_text())


@pytest.fixture(scope="module")
def raw():
    return ROLLBACK.read_text()


def test_rollback_exists():
    """Shipping a gate without a rollback path inverts the risk trade the gate
    was justified by: it raises the cost of a good promotion and leaves the cost
    of a bad one untouched."""
    assert ROLLBACK.exists()


def test_rollback_is_still_human_gated(wf):
    """Fast is not the same as unattended. Skipping QA is the concession; skipping
    the approvers is not."""
    assert wf["jobs"]["rollback"]["environment"] == "production"


def test_rollback_is_dispatch_only(wf):
    on = wf.get("on", wf.get(True))
    assert set(on) == {"workflow_dispatch"}


def test_rollback_cannot_introduce_non_production_bits(raw):
    """THE property. If a rollback could name an arbitrary ref it would be a
    supported way around the QA gate — 'roll back' to something never promoted."""
    assert "TARGET must be a bare tag or a sha256: digest within" in raw
    assert 'SRC="${REPO}@${TARGET}"' in raw and 'SRC="${REPO}:${TARGET}"' in raw


def test_rollback_refuses_a_cuda_minor_mismatch(raw):
    """Putting a 12.4 image on cuda-12.9-auto changes the CUDA userland under every
    instance resolving that tag — exactly the blast radius the gate exists for, and
    the easiest typo to make while typing a digest under pressure."""
    assert "CUDA_VERSION=" in raw and "REFUSING:" in raw


def test_rollback_performs_the_two_phase_dance(raw):
    """A plain re-tag can leave Vast's automatic-tag resolution serving the bad
    image: it resolves by newest push. A rollback that appears to succeed and does
    not take effect is the worst outcome available here."""
    assert "Phase A:" in raw and "Phase B:" in raw
    assert "no distinct anchor available" in raw, "silent fallback when no anchor exists"


def test_rollback_records_the_digest_it_moved_away_from(raw):
    """You often need to roll forward again once the real cause is found. If the
    superseded digest isn't recorded, it has to be reconstructed from registry
    archaeology during the incident."""
    assert "To undo this rollback" in raw


def test_rollback_shares_the_promote_concurrency_group(wf):
    """A rollback racing a promotion on the same tag gives an arbitrary winner."""
    assert wf["concurrency"]["group"] == "base-image-promote"


def test_rollback_dry_run_defaults_to_true(wf):
    on = wf.get("on", wf.get(True))
    assert on["workflow_dispatch"]["inputs"]["DRY_RUN"]["default"] is True


def test_rollback_requires_a_reason(wf):
    on = wf.get("on", wf.get(True))
    assert on["workflow_dispatch"]["inputs"]["REASON"]["required"] is True
