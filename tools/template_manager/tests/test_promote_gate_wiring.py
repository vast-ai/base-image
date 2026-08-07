"""The base promotion gate's wiring, pinned (ADR 0019 W7).

The safety properties here are structural — they live in the job graph, not in a
function — so they need structural tests. Each one below corresponds to a way the
gate could be silently disarmed by an ordinary-looking edit.
"""
import re
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


def test_promote_has_no_always_condition(wf):
    """Assert the parsed `if:`, not the job text — the text contains the word in a
    comment, and a test that greps for a string cannot tell a condition from prose."""
    cond = str(_job(wf, "promote").get("if", ""))
    assert "always()" not in cond, f"promote's if: is {cond!r}"


def test_a_collapsed_qa_phase_holds_everything_rather_than_flipping(raw):
    """promote IS reachable after a collapsed QA phase (qa-summary is always()).
    Safety then rests entirely on 'no evidence -> hold', so the decision read must
    be strict: a missing decisions.json must fail the step, not default open."""
    promote = _job_block(raw, "promote")
    assert "jq -er" in promote


def test_no_schedule_trigger(wf):
    """Nothing unattended may write a prod -auto tag (ADR 0019 cond 3)."""
    on = wf.get("on", wf.get(True))
    assert set(on) == {"workflow_dispatch"}, f"unexpected triggers: {set(on)}"


# --- fail closed -----------------------------------------------------------

def test_preflight_fails_when_the_key_is_missing(raw):
    block = raw.split("\n  preflight:", 1)[1].split("\n  resolve-digests:", 1)[0]
    assert "VAST_API_KEY" in block and "exit 1" in block


def test_qa_cells_are_fail_closed_and_assert_the_gpu_trio(wf):
    with_ = _job(wf, "qa")["with"]
    assert with_["require_key"] is True, "a missing key would skip and report green"
    for t in ("base/60-gpu-cuda", "base/61-cuda-compute", "base/62-gpu-libraries"):
        assert t in with_["require_tests"], f"{t} not required — it could skip green"


# --- the gate's actual decision --------------------------------------------

def _job_block(raw, name):
    """Slice one job's text. Scoping matters: this file previously asserted the
    gate's presence against the WHOLE workflow, and passed for six commits while
    the gate sat in the dry-run job and production flipped unguarded."""
    after = raw.split(f"\n  {name}:", 1)[1]
    return re.split(r"\n  (?=\S)", after, maxsplit=1)[0]


def test_the_hold_check_is_inside_the_promote_job(raw):
    """THE test. Must be scoped: a whole-file grep cannot tell a working gate from
    a copy of the gate sitting in a job that writes nothing."""
    promote = _job_block(raw, "promote")
    assert "decisions.json" in promote, "promote never reads the decisions"
    assert '"$decision" == "hold"' in promote, "promote does not act on a hold"


def test_the_hold_check_is_NOT_merely_somewhere_in_the_file(raw):
    """Relocation mutation: if the block moves to any other job, the assertion above
    must fail. Pinning that here means a future move cannot be masked by the string
    still existing elsewhere."""
    promote = _job_block(raw, "promote")
    elsewhere = raw.replace(promote, "")
    assert '"$decision" == "hold"' not in elsewhere, (
        "a hold check exists outside the promote job — if it is ALSO absent from "
        "promote the gate is disarmed, which is exactly how this shipped before"
    )


def test_promote_requires_decisions_rather_than_defaulting_open(raw):
    """`jq ... 2>/dev/null || echo ""` on a missing decisions.json would yield an
    empty decision and flip everything. -e makes absence fail the step."""
    promote = _job_block(raw, "promote")
    assert "jq -er" in promote, "decisions are read without -e; a missing file fails open"


def test_promote_points_auto_tags_at_the_approved_digest(raw):
    """Copy-by-digest (ADR 0019). A tag ref could have moved since approval."""
    promote = _job_block(raw, "promote")
    assert 'AUTO_TARGET_FINAL[$cuda_ver]="${NAMESPACE_PROD}/base-image@${target_digest}"' in promote


def test_promote_reverifies_the_MUTABLE_ref_before_writing(raw):
    """Approval can sit for hours; the dated staging tag is what can move and what
    gets copied. Re-resolving the run-scoped alias instead would be a tautology —
    this run created it at that digest and nothing else writes it."""
    promote = _job_block(raw, "promote")
    assert "aborting before any prod write" in promote
    assert "${tpl}-${dp}-" in promote, (
        "the drift check does not resolve the dated staging tag — if it re-resolves "
        "the run-scoped alias it can never disagree and proves nothing"
    )


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


# --- the required-test list, in every copy ---------------------------------

def test_every_copy_of_the_required_trio_agrees(raw, wf):
    """There are four independent copies of the GPU trio: the QA template's
    INSTANCE_TEST_REQUIRE_PASS, the qa job's require_tests input, qa-summary's
    REQUIRE_TESTS, and the linter's list. qa-summary's copy is the ACTUAL
    arbiter — it re-classifies every cell and decides flip/hold — and it was the
    one nothing pinned. Emptying it makes a GPU-trio self-skip classify as a pass
    and flip the tag, with the whole suite green."""
    import re as _re
    trio = {"base/60-gpu-cuda", "base/61-cuda-compute", "base/62-gpu-libraries"}

    cell = set(_job(wf, "qa")["with"]["require_tests"].split())
    assert cell == trio, f"qa job require_tests drifted: {cell}"

    m = _re.search(r'export REQUIRE_TESTS="([^"]*)"', _job_block(raw, "qa-summary"))
    assert m, "qa-summary no longer exports REQUIRE_TESTS — the arbiter lost its list"
    assert set(m.group(1).split()) == trio, (
        f"qa-summary's REQUIRE_TESTS is {m.group(1)!r}, not the trio. This copy "
        f"decides flip/hold; a weaker one silently reopens skip-as-pass.")

    tmpl = yaml.safe_load((REPO / "templates/base-qa/template.yml").read_text())
    env = tmpl.get("env", {}) if isinstance(tmpl, dict) else {}
    assert set(str(env.get("INSTANCE_TEST_REQUIRE_PASS", "")).split()) == trio, (
        "the QA template's required-pass list drifted from the CI-side one; the "
        "two layers are supposed to assert the SAME thing in different places")


def test_no_qa_bypass_input_exists(wf):
    """ADR 0019 amended 2026-08-07: an image that does not pass QA does not get
    promoted to an -auto tag via CI. Not a flag defaulting to off — absent."""
    on = wf.get("on", wf.get(True))
    inputs = set(on["workflow_dispatch"]["inputs"])
    assert not (inputs & {"SKIP_QA", "SKIP_REASON", "FORCE", "NO_QA"}), (
        f"a QA bypass input is back: {inputs}")


def test_the_qa_job_is_not_conditional_on_any_bypass(raw, wf):
    """The `if:` on the qa job is the other place a bypass would live."""
    cond = str(_job(wf, "qa").get("if", ""))
    for word in ("SKIP", "FORCE", "BYPASS"):
        assert word not in cond.upper(), f"qa job condition mentions {word}: {cond}"


def test_dry_run_defaults_to_false_so_a_plain_dispatch_really_promotes(wf):
    """Stated so the pair with SKIP_QA is deliberate rather than accidental."""
    on = wf.get("on", wf.get(True))
    assert on["workflow_dispatch"]["inputs"]["DRY_RUN"]["default"] is False
