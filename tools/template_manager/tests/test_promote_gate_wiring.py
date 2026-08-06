"""The base promotion gate's wiring, pinned (ADR 0019 W7).

The safety properties here are structural — they live in the job graph, not in a
function — so they need structural tests. Each one below corresponds to a way the
gate could be silently disarmed by an ordinary-looking edit.
"""
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
PROMOTE = REPO / ".github/workflows/promote-base-image.yml"


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(PROMOTE.read_text())


@pytest.fixture(scope="module")
def raw():
    return PROMOTE.read_text()


def _job(wf, name):
    assert name in wf["jobs"], f"job {name} is gone"
    return wf["jobs"][name]


# --- the approval still guards the prod write ------------------------------

def test_only_promote_carries_the_production_environment(wf):
    """The QA cells must NOT be environment-gated, or approval would be requested
    before any evidence exists — and the prod write must stay gated."""
    env_jobs = {n for n, j in wf["jobs"].items() if j.get("environment") == "production"}
    assert env_jobs == {"promote"}, f"production environment on unexpected jobs: {env_jobs}"


def test_promote_depends_on_the_qa_summary(wf):
    assert "qa-summary" in _job(wf, "promote")["needs"]


def test_promote_is_not_always(raw):
    """`if: always()` on promote would let it run after the QA phase collapsed,
    raising an approval prompt for a gate that never reported."""
    block = raw.split("\n  promote:", 1)[1].split("\n  ", 1)[0]
    assert "always()" not in block


def test_no_schedule_trigger(wf):
    """Nothing unattended may write a prod -auto tag (ADR 0019 cond 3)."""
    on = wf.get("on", wf.get(True))
    assert set(on) == {"workflow_dispatch"}, f"unexpected triggers: {set(on)}"


# --- fail closed -----------------------------------------------------------

def test_preflight_fails_when_the_key_is_missing(raw):
    block = raw.split("\n  preflight:", 1)[1].split("\n  resolve-digests:", 1)[0]
    assert "VAST_API_KEY" in block and "exit 1" in block


def test_skip_qa_requires_a_reason(raw):
    block = raw.split("\n  preflight:", 1)[1].split("\n  resolve-digests:", 1)[0]
    assert "SKIP_REASON" in block and "exit 1" in block


def test_qa_cells_are_fail_closed_and_assert_the_gpu_trio(wf):
    with_ = _job(wf, "qa")["with"]
    assert with_["require_key"] is True, "a missing key would skip and report green"
    for t in ("base/60-gpu-cuda", "base/61-cuda-compute", "base/62-gpu-libraries"):
        assert t in with_["require_tests"], f"{t} not required — it could skip green"


# --- the gate's actual decision --------------------------------------------

def test_promote_consults_the_decisions_before_flipping(raw):
    """The one line that makes this a gate: a held config keeps its pre-captured
    target instead of being pointed at the new build."""
    assert "decisions.json" in raw
    assert 'decision" == "hold"' in raw or '"$decision" == "hold"' in raw


def test_promote_reverifies_staging_before_writing(raw):
    """Approval can sit for hours; staging tags are mutable."""
    block = raw.split("\n  promote:", 1)[1]
    assert "aborting before any prod write" in block


def test_qa_only_tests_configs_whose_digest_changes(raw):
    """Re-certifying an unchanged digest would rent GPUs to prove nothing, and
    would let an unrelated flake block a no-op re-push."""
    assert ".target_digest != .current_digest" in raw


# --- bounded against the single-key account --------------------------------

def test_qa_matrix_parallelism_is_bounded(wf):
    mp = _job(wf, "qa")["strategy"]["max-parallel"]
    assert mp <= 3, f"max-parallel {mp} would storm the single-key QA account (ADR 0005 cond 6)"


def test_qa_evidence_names_are_per_cell(wf):
    """upload-artifact@v4 rejects duplicate names within a run, so a fixed name
    would fail every cell after the first."""
    assert "${{ matrix.key }}" in _job(wf, "qa")["with"]["evidence_name"]


def test_each_cell_gets_its_own_driver_floor(wf):
    assert "${{ matrix.floor }}" in _job(wf, "qa")["with"]["set_filters"]


def test_cells_test_the_run_scoped_pinned_ref(wf):
    """Testing the dated tag would let a concurrent rebuild swap the bits under
    the test; the run-scoped alias is written once and cannot move."""
    tag = _job(wf, "qa")["with"]["tag"]
    assert "github.run_id" in tag and "matrix.key" in tag


def test_qa_summary_runs_even_when_a_cell_failed(raw):
    """The run with a red cell is exactly the one whose table matters."""
    block = raw.split("\n  qa-summary:", 1)[1].split("\n  promote:", 1)[0]
    assert "always()" in block
