"""What the promote job DOES, not what its YAML says (ADR 0019).

The companion file test_promote_gate_wiring.py pins the job graph — which job
carries the production environment, what needs what. These tests execute the
dance script itself against a fake registry and assert the only thing the safety
claim is actually about:

    after this runs, what digest does each cuda-X.Y.Z-auto tag hold?

Every test here corresponds to a disarm that the string-matching tests miss.
Three of them are not hypothetical: two shipped, and the third was found by
review of the commit that fixed the first two.
"""

from __future__ import annotations

import json

import pytest

from wfexec import (DATE, PROMOTE, PROD, auto_tag, build_registry, decisions, manifest,
                    promote_env, requires_tools, run_step, step_script)

pytestmark = pytest.mark.skipif(not requires_tools(), reason="jq not available")

DANCE = "Promote all images sequentially"

OLD = {"cuda-12.9-24": "sha256:old12", "cuda-13.3-24": "sha256:old13"}
NEW = {"cuda-12.9-24": "sha256:new12", "cuda-13.3-24": "sha256:new13"}
AUTOS = {"12.9.2": "sha256:old12", "13.3.1": "sha256:old13"}


def _run(tmp_path, verdicts, targets=None, current=None, registry=None, extra_digests=()):
    targets = targets or NEW
    current = current if current is not None else OLD
    wd = tmp_path / "wd"
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "manifest.json").write_text(json.dumps(manifest(targets, current)))
    if verdicts is not None:
        (wd / "decisions.json").write_text(json.dumps(decisions(verdicts, targets)))
    return run_step(step_script(PROMOTE, "promote", DANCE), wd,
                    registry if registry is not None else build_registry(targets, AUTOS),
                    promote_env(), extra_digests=extra_digests)


# --- THE property ----------------------------------------------------------

def test_a_hold_leaves_the_auto_tag_on_its_old_digest(tmp_path):
    """The single most important assertion in this repo. A held config's auto tag
    must still resolve to the digest it held before the run."""
    r = _run(tmp_path, {"cuda-12.9-24": "hold", "cuda-13.3-24": "flip"})
    assert r["code"] == 0, r["err"]
    assert r["registry"][auto_tag("12.9.2")] == "sha256:old12", (
        "a HELD auto tag moved — the gate is disarmed")


def test_a_flip_moves_the_auto_tag_to_the_tested_digest(tmp_path):
    """The gate must also not be uselessly strict: an approved flip has to land."""
    r = _run(tmp_path, {"cuda-12.9-24": "hold", "cuda-13.3-24": "flip"})
    assert r["registry"][auto_tag("13.3.1")] == "sha256:new13"


def test_deleting_the_continue_from_the_hold_branch_is_caught(tmp_path):
    """The mutation that survives every string-matching test: keep the `if`, keep
    the log line, drop the one word that makes the hold a hold. Execution sees it
    because the tag moves."""
    script = step_script(PROMOTE, "promote", DANCE)
    i = script.index('"$decision" == "hold"')
    head, tail = script[:i], script[i:]
    neutered = head + tail.replace("continue\n", "", 1)
    assert neutered != script

    wd = tmp_path / "mut"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps(manifest(NEW, OLD)))
    (wd / "decisions.json").write_text(json.dumps(
        decisions({"cuda-12.9-24": "hold", "cuda-13.3-24": "flip"}, NEW)))
    r = run_step(neutered, wd, build_registry(NEW, AUTOS), promote_env())
    assert r["registry"][auto_tag("12.9.2")] == "sha256:new12", (
        "the mutation did not actually disarm the gate — this test proves nothing")
    # ...and the real script must therefore differ in outcome, which the first
    # test above asserts. Stated here so the pair reads as one argument.


def test_every_hold_holds_when_nothing_passed(tmp_path):
    """A total QA failure must move nothing at all."""
    r = _run(tmp_path, {"cuda-12.9-24": "hold", "cuda-13.3-24": "hold"})
    assert r["code"] == 0, r["err"]
    for cuda_ver, digest in AUTOS.items():
        assert r["registry"][auto_tag(cuda_ver)] == digest


# --- fail closed -----------------------------------------------------------

def test_a_missing_decisions_file_writes_no_auto_tag(tmp_path):
    """`jq -er` must make absence fatal. A decisions.json that failed to download
    is the fail-open shape that matters: it looks like 'no holds'."""
    r = _run(tmp_path, None)
    assert r["code"] != 0
    for cuda_ver, digest in AUTOS.items():
        assert r["registry"].get(auto_tag(cuda_ver)) == digest


def test_a_decision_for_an_unknown_config_writes_no_auto_tag(tmp_path):
    """decisions.json present but not covering a config being promoted. The read
    must fail rather than treat 'no row' as 'not held'."""
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps(manifest(NEW, OLD)))
    partial = [d for d in decisions({"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"}, NEW)
               if d["key"] != "cuda-12.9-24"]
    (wd / "decisions.json").write_text(json.dumps(partial))
    r = run_step(step_script(PROMOTE, "promote", DANCE), wd,
                 build_registry(NEW, AUTOS), promote_env())
    assert r["code"] != 0
    assert r["registry"][auto_tag("12.9.2")] == "sha256:old12"


def test_a_missing_staging_source_aborts_before_any_prod_write(tmp_path):
    """Pre-flight exists so a half-done promotion is impossible. Verify it stops
    the run rather than merely logging."""
    reg = build_registry(NEW, AUTOS)
    del reg["staging/base-image:cuda-12.9.2-cudnn-devel-ubuntu24.04-py312-2026-08-07"]
    r = _run(tmp_path, {"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"}, registry=reg)
    assert r["code"] != 0
    assert r["registry"][auto_tag("12.9.2")] == "sha256:old12"
    assert r["registry"][auto_tag("13.3.1")] == "sha256:old13", (
        "the other config was promoted anyway — pre-flight is not gating the writes")


# --- the dance itself ------------------------------------------------------

def test_the_flip_is_a_two_phase_push(tmp_path):
    """Vast resolves the auto tag by newest push, so a flip must write the tag
    twice: once to a distinct anchor, then to the target. One write would leave
    the ordering unchanged and the promotion invisible to customers."""
    r = _run(tmp_path, {"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"})
    writes = [c for c in r["crane_calls"]
              if c.startswith("copy") and c.endswith(auto_tag("12.9.2"))]
    assert len(writes) == 2, f"expected anchor + target, got {writes}"
    assert r["registry"][auto_tag("12.9.2")] == "sha256:new12"


def test_a_held_tag_is_re_pushed_at_its_own_digest_never_the_target(tmp_path):
    """A held tag is not left alone — every auto tag is re-pushed every run because
    push ORDER is what Vast resolves on. So the assertion is not "no write", it is
    "no write of the untested digest": Phase A parks it on an anchor and Phase B
    must bring it back to where it started, not forward to what QA rejected."""
    r = _run(tmp_path, {"cuda-12.9-24": "hold", "cuda-13.3-24": "flip"})
    writes = [c for c in r["crane_calls"]
              if c.startswith("copy") and c.endswith(auto_tag("12.9.2"))]
    assert len(writes) == 2, f"expected the anchor+restore pair, got {writes}"
    assert not any("new12" in w for w in writes), (
        f"the REJECTED digest was pushed to a held tag: {writes}")
    assert r["registry"][auto_tag("12.9.2")] == "sha256:old12"


def test_the_default_python_is_copied_by_digest_not_by_tag(tmp_path):
    """The auto tag points at the default-python artifact, so that one is copied
    by digest: a mutable staging tag could have moved since approval."""
    r = _run(tmp_path, {"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"})
    by_digest = [c for c in r["crane_calls"] if c.startswith("copy") and "@sha256:" in c]
    assert by_digest, "no copy-by-digest at all"


def test_an_unchanged_digest_still_re_pushes_the_auto_tag(tmp_path):
    """The dance runs every promotion, changed or not — push ORDER is what Vast
    resolves on. A 'nothing changed so skip it' optimisation would silently stop
    advancing the tag."""
    r = _run(tmp_path, {"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"},
             targets=OLD, current=OLD, registry=build_registry(OLD, AUTOS))
    assert r["code"] == 0, r["err"]
    writes = [c for c in r["crane_calls"]
              if c.startswith("copy") and c.endswith(auto_tag("12.9.2"))]
    assert len(writes) == 2


# --- drift -----------------------------------------------------------------

def test_staging_moving_after_approval_aborts_before_any_prod_write(tmp_path):
    """Approval can sit for hours and the dated staging tag is mutable. The
    drift step is a separate step, so run it separately — and prove it aborts."""
    wd = tmp_path / "drift"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps(manifest(NEW, OLD)))
    (wd / "decisions.json").write_text(json.dumps(
        decisions({"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"}, NEW)))
    reg = build_registry(NEW, AUTOS)
    reg["staging/base-image:cuda-12.9.2-cudnn-devel-ubuntu24.04-py312-2026-08-07"] = \
        "sha256:REBUILT-SINCE-APPROVAL"
    r = run_step(step_script(PROMOTE, "promote", "Re-verify the decisions still describe staging"),
                 wd, reg, {"NAMESPACE_STAGING": "staging", "STAGING_DATE": "2026-08-07"})
    assert r["code"] != 0, "drift did not abort the promotion"
    assert "aborting before any prod write" in r["out"] + r["err"]


def test_no_drift_lets_the_promotion_proceed(tmp_path):
    """The drift check must not be a tripwire that fires on the happy path."""
    wd = tmp_path / "nodrift"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps(manifest(NEW, OLD)))
    (wd / "decisions.json").write_text(json.dumps(
        decisions({"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"}, NEW)))
    r = run_step(step_script(PROMOTE, "promote", "Re-verify the decisions still describe staging"),
                 wd, build_registry(NEW, AUTOS),
                 {"NAMESPACE_STAGING": "staging", "STAGING_DATE": "2026-08-07"})
    assert r["code"] == 0, r["err"]


def test_a_staging_date_containing_shell_is_data_not_code(tmp_path):
    """STAGING_DATE is free dispatch text reaching the job that holds prod
    credentials. It must arrive via env: so the shell never parses it — this
    failed once as `eval` of a verdict reason, and once here."""
    wd = tmp_path / "inj"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps(manifest(NEW, OLD)))
    (wd / "decisions.json").write_text(json.dumps(
        decisions({"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"}, NEW)))
    canary = wd / "pwned"
    r = run_step(step_script(PROMOTE, "promote", "Re-verify the decisions still describe staging"),
                 wd, build_registry(NEW, AUTOS),
                 {"NAMESPACE_STAGING": "staging",
                  "STAGING_DATE": f'$(touch {canary})"; touch {canary}; #'})
    assert not canary.exists(), "the dispatch input executed as shell"
    assert r["code"] != 0, "a nonsense date should fail the drift check, not pass it"


# --- the dry run: the operator's pre-promote sanity check ------------------

PLAN = "Print promotion plan"


def _plan(tmp_path, registry=None, targets=None):
    targets = targets or NEW
    return run_step(step_script(PROMOTE, "dry-run", PLAN), tmp_path / "plan",
                    registry if registry is not None else build_registry(targets, AUTOS),
                    promote_env())


def test_the_dry_run_completes(tmp_path):
    """It had no test of any kind, which is why a commit could delete four
    assignments from its loop and ship. ADR 0019 makes this the FIRST staged
    exercise of the gate, so a dry run that dies or lies is not cosmetic."""
    r = _plan(tmp_path)
    assert r["code"] == 0, r["err"]


def test_the_dry_run_resolves_a_distinct_final_target_per_config(tmp_path):
    """The regression: the loop body was deleted, so every iteration wrote to one
    stale key using the last config's tag_template. The plan then reported 11 of
    12 auto tags as unchanged and one as pointing at another config's image."""
    r = _plan(tmp_path)
    out = r["out"]
    for cuda_ver, digest in (("12.9.2", "sha256:new12"), ("13.3.1", "sha256:new13")):
        assert f"cuda-{cuda_ver}-auto" in out, f"no plan line for cuda-{cuda_ver}-auto"
    # Each auto tag's planned target must be its OWN config's image, so both new
    # digests appear. A single leaked tag_template collapses these to one.
    assert "new12" in out and "new13" in out, (
        "the plan does not name a distinct target per config — the loop is "
        "writing every config's target to one key")


def test_the_dry_run_survives_a_namespace_with_no_auto_tags_yet(tmp_path):
    """With AUTO_TARGET_PRE empty, an unassigned cuda_ver makes the array write
    `bad array subscript`, which is fatal regardless of set -e. A first-run or
    scratch namespace is exactly when someone reaches for the dry run."""
    reg = {k: v for k, v in build_registry(NEW, AUTOS).items() if "-auto" not in k}
    r = _plan(tmp_path, registry=reg)
    assert r["code"] == 0, r["err"]
    assert "bad array subscript" not in r["err"]


def test_the_dry_run_writes_nothing(tmp_path):
    """It is the rehearsal. If it can write, it is not one."""
    before = build_registry(NEW, AUTOS)
    r = _plan(tmp_path, registry=dict(before))
    assert r["registry"] == before, "the dry run mutated the registry"
    assert not [c for c in r["crane_calls"] if c.startswith("copy")]


# --- a same-day rebuild must not change what ships -------------------------

def _rebuild_staging(reg, key, tag_template, only_py=None):
    """Simulate a rebuild rewriting the MUTABLE dated staging tags after the plan
    was pinned. `only_py` reproduces a FILTERed/partial rebuild, which is the case
    the old default-python drift proxy could not see."""
    out = dict(reg)
    for py in ("310", "311", "312", "313", "314"):
        if only_py and py != only_py:
            continue
        k = f"staging/base-image:{tag_template}-py{py}-{DATE}"
        if k in out:
            out[k] = f"sha256:REBUILT-{py}"
    return out


def test_a_partial_rebuild_of_a_non_default_python_cannot_reach_prod(tmp_path):
    """THE case that motivated pinning everything. py310 carries no auto tag, so
    the default-python drift check does not see it move — and py310 used to be
    copied by TAG, so the rebuilt bits would have shipped under the prod dated
    tag with nothing detecting it."""
    base = build_registry(NEW, AUTOS)
    reg = _rebuild_staging(base, "cuda-12.9-24",
                           "cuda-12.9.2-cudnn-devel-ubuntu24.04", only_py="310")
    r = _run(tmp_path, {"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"}, registry=reg,
             extra_digests=set(base.values()))
    assert r["code"] == 0, r["err"]
    shipped = r["registry"]["prod/base-image:cuda-12.9.2-cudnn-devel-ubuntu24.04-py310-" + DATE]
    assert shipped == "sha256:other-cuda-12.9-24-310", (
        f"a post-plan rebuild reached prod: py310 shipped {shipped}")
    assert not shipped.startswith("sha256:REBUILT"), "rebuilt bits were promoted"


def test_no_prod_tag_is_ever_copied_from_a_mutable_staging_tag(tmp_path):
    """Structural version of the same property, over the whole run: every copy
    into prod must name a digest, never a dated staging tag. A single by-tag copy
    reopens the hole for whichever artifact it covers."""
    r = _run(tmp_path, {"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"})
    assert r["code"] == 0, r["err"]
    by_tag = [c for c in r["crane_calls"]
              if c.startswith("copy") and f"-{DATE}" in c.split()[1] and "@sha256:" not in c.split()[1]]
    assert by_tag == [], f"{len(by_tag)} prod copies read a mutable staging tag: {by_tag[:3]}"


def test_a_missing_pinned_digest_fails_rather_than_falling_back_to_the_tag(tmp_path):
    """The dangerous repair is `|| SOURCE=<tag>`: it would restore the old
    behaviour silently on exactly the config whose pin went missing."""
    wd = tmp_path / "nopin"
    wd.mkdir()
    m = manifest(NEW, OLD)
    del m["configs"][0]["py_digests"]["py310"]
    (wd / "manifest.json").write_text(json.dumps(m))
    (wd / "decisions.json").write_text(json.dumps(
        decisions({"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"}, NEW)))
    r = run_step(step_script(PROMOTE, "promote", DANCE), wd,
                 build_registry(NEW, AUTOS), promote_env())
    assert r["code"] != 0, "a missing pin did not fail the step"
    assert "refusing to copy by mutable tag" in r["out"] + r["err"]


def test_a_full_rebuild_still_aborts_via_the_default_python_drift_check(tmp_path):
    """Pinning does not make the drift check redundant: when default-python moves,
    the QA evidence no longer describes what staging holds, and the operator should
    be told rather than silently shipping older-but-approved bits."""
    wd = tmp_path / "drift2"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps(manifest(NEW, OLD)))
    (wd / "decisions.json").write_text(json.dumps(
        decisions({"cuda-12.9-24": "flip", "cuda-13.3-24": "flip"}, NEW)))
    reg = _rebuild_staging(build_registry(NEW, AUTOS), "cuda-12.9-24",
                           "cuda-12.9.2-cudnn-devel-ubuntu24.04")
    r = run_step(step_script(PROMOTE, "promote", "Re-verify the decisions still describe staging"),
                 wd, reg, {"NAMESPACE_STAGING": "staging", "STAGING_DATE": DATE})
    assert r["code"] != 0
    assert "aborting before any prod write" in r["out"] + r["err"]
