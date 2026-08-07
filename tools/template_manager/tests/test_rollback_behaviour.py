"""The rollback escape hatch, executed (ADR 0019).

The string-matching companion (test_rollback_workflow.py) asserted things like
`"CUDA_VERSION=" in raw and "REFUSING:" in raw` — both of which live inside a jq
selector and an echo, so inverting or deleting the guard did not move the test.
It also could not see the defect that actually shipped: the version regex
accepted `12.9` and refused `12.9.2`, while every auto tag that exists is
patch-versioned. The escape hatch was inert and eleven green tests said otherwise.

These run the validate step. The tag names are derived from the real
configs/base-image.json the same way promote derives them, so a change to the
tag scheme breaks these tests rather than silently breaking the rollback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from wfexec import ROLLBACK, requires_tools, run_step, step_script

pytestmark = pytest.mark.skipif(not requires_tools(), reason="jq not available")

REPO = Path(__file__).resolve().parents[3]
STEP = "Resolve and validate"
PROD = "prod"

CUR = "sha256:bad-current"
PREV = "sha256:known-good"
OTHER = "sha256:another-cuda"


def real_auto_versions():
    """Exactly how promote derives an auto tag name from the shared config table."""
    cfg = json.loads((REPO / "configs/base-image.json").read_text())
    out = []
    for c in cfg["configs"]:
        m = re.match(r"^cuda-([0-9.]*)-", c["tag_template"])
        if m and m.group(1):
            out.append(m.group(1))
    return out


def _mm(v):
    """major.minor of an auto-tag version, which may be 2- or 3-component."""
    parts = v.split(".")
    return ".".join(parts[:2])


def _dated(cuda_ver):
    """A previously-promoted prod dated tag for this config — a legitimate target."""
    return f"cuda-{_mm(cuda_ver)}.0-cudnn-devel-ubuntu24.04-py312-2026-07-01"


def _registry(cuda_ver):
    return {
        f"{PROD}/base-image:cuda-{cuda_ver}-auto": CUR,
        f"{PROD}/base-image:{_dated(cuda_ver)}": PREV,
        # A second, distinct auto tag so Phase A has an anchor to find.
        f"{PROD}/base-image:cuda-99.9.9-auto": OTHER,
    }


def _configs(cuda_ver):
    mm = _mm(cuda_ver)
    return {
        PREV: {"config": {"Env": [f"CUDA_VERSION={mm}.1", "PATH=/usr/bin"]}},
        CUR: {"config": {"Env": [f"CUDA_VERSION={mm}.2"]}},
        OTHER: {"config": {"Env": ["CUDA_VERSION=99.9.0"]}},
    }


def _run(tmp_path, cuda_ver, target, registry=None, configs=None):
    return run_step(
        step_script(ROLLBACK, "rollback", STEP), tmp_path / "rb",
        registry if registry is not None else _registry(cuda_ver),
        {"NAMESPACE_PROD": PROD, "CUDA_VERSION": cuda_ver, "TARGET": target,
         "REASON": "incident"},
        configs=configs if configs is not None else _configs(cuda_ver or "0.0"))


# --- the defect that shipped ------------------------------------------------

def test_every_auto_tag_the_repo_actually_produces_is_accepted(tmp_path):
    """THE test. Not a hand-written list of plausible versions — the versions the
    shared config table really yields. The shipped regex refused all of them."""
    versions = real_auto_versions()
    assert versions, "config table produced no auto versions — fixture is wrong"
    for v in versions:
        r = run_step(step_script(ROLLBACK, "rollback", STEP), tmp_path / f"v{v}",
                     _registry(v),
                     {"NAMESPACE_PROD": PROD, "CUDA_VERSION": v,
                      "TARGET": _dated(v),
                      "REASON": "incident"},
                     configs=_configs(v))
        assert r["code"] == 0, f"cuda-{v}-auto was refused: {r['err']}"


def test_a_patch_versioned_rollback_moves_the_tag(tmp_path):
    r = _run(tmp_path, "12.9.2", _dated("12.9.2"))
    assert r["code"] == 0, r["err"]
    assert "src_digest=" + PREV in r["github_output"]


# --- it cannot become a way to ship untested bits ---------------------------

@pytest.mark.parametrize("target", [
    "ghcr.io/someone/base-image:evil",
    "staging/base-image:cuda-12.9.2-cudnn-devel-ubuntu24.04-py312-2026-08-07",
    "otheruser/base-image:tag",
    "prod/other-repo:tag",
])
def test_a_target_outside_the_production_repo_is_refused(tmp_path, target):
    """If rollback could name an arbitrary ref it would be a supported route
    around the QA gate: 'roll back' to something never promoted."""
    r = _run(tmp_path, "12.9.2", target)
    assert r["code"] != 0, f"{target} was accepted"
    assert "TARGET must be" in r["out"] + r["err"]


def test_a_bare_digest_within_production_is_accepted(tmp_path):
    r = _run(tmp_path, "12.9.2", PREV)
    assert r["code"] == 0, r["err"]


def test_a_digest_that_does_not_exist_is_refused(tmp_path):
    r = _run(tmp_path, "12.9.2", "sha256:never-existed")
    assert r["code"] != 0


# --- the 3am typo -----------------------------------------------------------

def test_an_image_of_a_different_cuda_minor_is_refused(tmp_path):
    """Putting a 12.4 image on cuda-12.9.2-auto changes the CUDA userland under
    every instance resolving that tag. It looks exactly like a correct command."""
    cfgs = _configs("12.9.2")
    cfgs[PREV] = {"config": {"Env": ["CUDA_VERSION=12.4.1"]}}
    r = _run(tmp_path, "12.9.2",
             _dated("12.9.2"), configs=cfgs)
    assert r["code"] != 0, "a cross-minor rollback was permitted"
    assert "REFUSING" in r["out"] + r["err"]


def test_a_different_patch_of_the_same_minor_is_allowed(tmp_path):
    """The guard must not be so tight that it blocks the normal case — rolling
    12.9.2 back to 12.9.1 is the whole point."""
    cfgs = _configs("12.9.2")
    cfgs[PREV] = {"config": {"Env": ["CUDA_VERSION=12.9.1"]}}
    r = _run(tmp_path, "12.9.2",
             _dated("12.9.2"), configs=cfgs)
    assert r["code"] == 0, r["err"]


def test_an_image_with_no_cuda_version_is_refused_rather_than_guessed(tmp_path):
    cfgs = _configs("12.9.2")
    cfgs[PREV] = {"config": {"Env": ["PATH=/usr/bin"]}}
    r = _run(tmp_path, "12.9.2",
             _dated("12.9.2"), configs=cfgs)
    assert r["code"] != 0
    assert "Refusing to guess" in r["out"] + r["err"]


@pytest.mark.parametrize("bad", ["12", "twelve.nine", "12.9.2.1", "", "12.9.2 "])
def test_a_malformed_version_is_refused(tmp_path, bad):
    assert _run(tmp_path, bad, PREV)["code"] != 0


# --- it must not silently no-op ---------------------------------------------

def test_rolling_back_to_the_current_digest_is_refused(tmp_path):
    """A rollback that changes nothing but reports success is the worst outcome
    available here — the operator believes the incident is mitigated."""
    r = _run(tmp_path, "12.9.2", CUR)
    assert r["code"] != 0
    assert "already points at" in r["out"] + r["err"]


def test_a_nonexistent_auto_tag_is_refused(tmp_path):
    """Typing an accepted-but-wrong version must not create a brand-new auto tag
    that nothing resolves and nobody is watching."""
    reg = _registry("12.9.2")
    del reg[f"{PROD}/base-image:cuda-12.9.2-auto"]
    r = _run(tmp_path, "12.9.2",
             _dated("12.9.2"), registry=reg)
    assert r["code"] != 0
    assert "promote instead" in r["out"] + r["err"]


def test_an_anchor_is_found_among_patch_versioned_tags(tmp_path):
    """Phase A needs a digest distinct from both current and target. The anchor
    scan matched only two-component names, so it never found one and every
    rollback printed the 'no distinct anchor' warning."""
    r = _run(tmp_path, "12.9.2", _dated("12.9.2"))
    assert r["code"] == 0, r["err"]
    anchor = [l for l in r["github_output"].splitlines() if l.startswith("anchor=")]
    assert anchor and anchor[0] != "anchor=", (
        "no Phase A anchor resolved — the dance degrades to a single push")


def test_the_superseded_digest_is_reported_for_rolling_forward(tmp_path):
    r = _run(tmp_path, "12.9.2", _dated("12.9.2"))
    assert "To undo this rollback" in r["summary"]
    assert CUR in r["summary"]


def test_an_incident_reason_containing_a_quote_does_not_break_the_hatch(tmp_path):
    """The escape hatch must survive the input it is designed to receive. A
    reason like: image "cuda-12.9" broke — was a bash syntax error."""
    r = run_step(step_script(ROLLBACK, "rollback", STEP), tmp_path / "q",
                 _registry("12.9.2"),
                 {"NAMESPACE_PROD": PROD, "CUDA_VERSION": "12.9.2",
                  "TARGET": _dated("12.9.2"),
                  "REASON": 'image "cuda-12.9" broke; $(touch pwned) 100% of the time'},
                 configs=_configs("12.9.2"))
    assert r["code"] == 0, r["err"]
    assert not (tmp_path / "q" / "pwned").exists(), "the reason executed as shell"
    assert 'image "cuda-12.9" broke' in r["summary"]
