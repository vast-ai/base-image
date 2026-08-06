"""The base gate's safety property, pinned (ADR 0019 cond 2).

    An auto tag may only move to a digest that PASSED live-GPU QA in this run,
    on that exact digest.

The mutation that matters most: evidence exists and passed, but for a DIFFERENT
digest than the one about to ship. Staging date-tags are mutable — a same-day
partial rebuild rewrites them — so "this config passed" is not the same claim as
"these bits passed", and only the second one is safe.
"""

from __future__ import annotations

import pytest

from verify_qa_evidence import classify_configs, render_summary

D_OLD = "sha256:" + "a" * 64
D_NEW = "sha256:" + "b" * 64
D_OTHER = "sha256:" + "c" * 64


def cfg(key="cuda-13.0-24", auto="13.0.3", target=D_NEW, current=D_OLD):
    return {"key": key, "tag_template": f"cuda-{auto}-cudnn-devel-ubuntu24.04",
            "auto_version": auto, "target_digest": target, "current_digest": current}


def manifest(*configs):
    return {"configs": list(configs)}


def only(decisions, key):
    return next(d for d in decisions if d["key"] == key)


# --- the happy path --------------------------------------------------------

def test_passing_evidence_on_the_exact_digest_flips():
    d = classify_configs(manifest(cfg()),
                         {"cuda-13.0-24": {"verdict": "pass", "digest": D_NEW}})
    assert only(d, "cuda-13.0-24")["decision"] == "flip"


# --- THE mutation ----------------------------------------------------------

def test_evidence_for_a_different_digest_holds():
    """Passed — but on bits that are not the bits shipping. Mutable staging tags
    make this reachable, not theoretical."""
    d = only(classify_configs(manifest(cfg()),
                              {"cuda-13.0-24": {"verdict": "pass", "digest": D_OTHER}}),
             "cuda-13.0-24")
    assert d["decision"] == "hold"
    assert "does not cover" in d["reason"]


def test_missing_evidence_holds():
    d = only(classify_configs(manifest(cfg()), {}), "cuda-13.0-24")
    assert d["decision"] == "hold"
    assert "no QA evidence" in d["reason"]


@pytest.mark.parametrize("verdict", ["block", "inconclusive", None, ""])
def test_non_passing_verdict_holds(verdict):
    d = only(classify_configs(manifest(cfg()),
                              {"cuda-13.0-24": {"verdict": verdict, "digest": D_NEW}}),
             "cuda-13.0-24")
    assert d["decision"] == "hold"


def test_missing_target_digest_holds():
    d = only(classify_configs(manifest(cfg(target=None)),
                              {"cuda-13.0-24": {"verdict": "pass", "digest": D_NEW}}),
             "cuda-13.0-24")
    assert d["decision"] == "hold"


# --- the cases the flip-based framing makes free ---------------------------

def test_unchanged_digest_needs_no_evidence():
    """The dance re-pushes every auto tag each run for ORDERING, changed or not.
    A re-push of identical content is not a flip and must not demand a fresh test —
    otherwise an unrelated config's flake would block a no-op."""
    d = only(classify_configs(manifest(cfg(target=D_OLD, current=D_OLD)), {}),
             "cuda-13.0-24")
    assert d["decision"] == "flip"
    assert "unchanged" in d["reason"]


def test_stock_configs_have_no_auto_tag():
    d = only(classify_configs(manifest(cfg(key="stock-24", auto=None)), {}), "stock-24")
    assert d["decision"] == "n/a"


def test_a_new_auto_tag_still_requires_evidence():
    """current_digest None = the tag does not exist yet (a patch bump renames it,
    e.g. cuda-13.3.0-auto -> cuda-13.3.1-auto). Creating it is still a flip."""
    d = only(classify_configs(manifest(cfg(current=None)), {}), "cuda-13.0-24")
    assert d["decision"] == "hold"


# --- one flaky cell must not block the fleet -------------------------------

def test_a_single_hold_does_not_affect_the_others():
    """The whole reason `hold` is not `abort`: eleven good configs still ship."""
    m = manifest(cfg(key="a", auto="12.9"), cfg(key="b", auto="13.0"), cfg(key="c", auto="13.1"))
    ev = {"a": {"verdict": "pass", "digest": D_NEW},
          "b": {"verdict": "block", "digest": D_NEW},
          "c": {"verdict": "pass", "digest": D_NEW}}
    d = classify_configs(m, ev)
    assert [x["decision"] for x in d] == ["flip", "hold", "flip"]


# --- the break-glass, and its visibility -----------------------------------

def test_skip_qa_flips_without_evidence_but_says_so():
    d = only(classify_configs(manifest(cfg()), {}, skip_qa=True), "cuda-13.0-24")
    assert d["decision"] == "flip"
    assert "WITHOUT automated evidence" in d["reason"]


def test_summary_shouts_about_skip_qa():
    out = render_summary(classify_configs(manifest(cfg()), {}, skip_qa=True))
    assert "SKIP_QA was used" in out


def test_summary_leads_with_holds_and_names_them():
    """The approver must see a held tag without reading the whole table."""
    m = manifest(cfg(key="a", auto="12.9"), cfg(key="b", auto="13.0"))
    ev = {"a": {"verdict": "pass", "digest": D_NEW}}
    out = render_summary(classify_configs(m, ev))
    assert "will NOT be updated" in out
    assert "cuda-13.0-auto" in out
    assert "**HOLD**" in out


def test_summary_omits_configs_with_no_auto_tag():
    out = render_summary(classify_configs(manifest(cfg(key="stock-24", auto=None)), {}))
    assert "stock-24" not in out
