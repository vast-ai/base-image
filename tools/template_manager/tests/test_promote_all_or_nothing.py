"""Guard: promotion is all-or-nothing, and a bad DRAW is retried not held.

This supersedes ADR 0019's "flip passing / hold failing". That rule was chosen
because atomic gating is p^n in per-cell reliability (~54% clean at 12 cells x
95%), which predictably drives operators to route around the gate.

The answer to that is not partial promotion — it is removing the flakiness. On
2026-08-14 the single hold was a host that presented NO GPU: 60/61/62 skipped,
`skip != pass` blocked the cell, and one bad draw would have held a tag on an
image that was fine. qa-gate.yml now draws a fresh host when a cell blocks with
zero failed tests, which is what the reliability argument was really about.

What partial promotion assumed, and should not have: that a red cell is evidence
about one image. Every config shares the same ROOT overlay and boot path, so a
genuine defect on cuda-12.6 is evidence about all twelve; flipping the other
eleven is the least safe reading of it.
"""
import json
import re
import subprocess
from pathlib import Path

import yaml

_REDRAW_GUARD = re.compile(r'if \{? ?\[ "\$CODE" -eq 1 \].*?\n            fi', re.S)

REPO = Path(__file__).resolve().parents[3]
PROMOTE = REPO / ".github" / "workflows" / "promote-base-image.yml"
QA_GATE = REPO / ".github" / "workflows" / "qa-gate.yml"


def _decide_step() -> str:
    steps = yaml.safe_load(PROMOTE.read_text())["jobs"]["qa-summary"]["steps"]
    for s in steps:
        if "Decide" in str(s.get("name", "")):
            return s["run"]
    raise AssertionError("no 'Decide which auto tags may flip' step")


def _atomic_jq() -> str:
    m = re.search(r"jq '(\[\.\[\] \| if \.decision == \"flip\".*?end\])'", _decide_step(), re.S)
    assert m, "could not find the all-or-nothing rewrite in the Decide step"
    return m.group(1)


def _apply(decisions: list) -> list:
    """Model the workflow's control flow, not just its jq.

    The rewrite runs INSIDE `if [ "$_holds" -gt 0 ]`, so applying it
    unconditionally would test something the workflow never does — and would
    report a false failure on a clean batch.
    """
    if not [d for d in decisions if d["decision"] == "hold"]:
        return decisions
    r = subprocess.run(["jq", _atomic_jq()], input=json.dumps(decisions),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


DECISIONS = [
    {"key": "stock-22", "decision": "n/a", "reason": "no auto tag for this config"},
    {"key": "cuda-12.6-24", "decision": "hold", "reason": "failed: a real test failure"},
    {"key": "cuda-13.3-24", "decision": "flip", "reason": "QA passed on this exact digest"},
]


def test_one_hold_converts_every_flip():
    out = _apply(DECISIONS)
    assert not [d for d in out if d["decision"] == "flip"], (
        "a flip survived alongside a hold — promotion is not all-or-nothing")


def test_the_real_cause_is_preserved_on_the_genuine_hold():
    out = {d["key"]: d for d in _apply(DECISIONS)}
    assert out["cuda-12.6-24"]["reason"] == "failed: a real test failure", (
        "the originating failure's reason was overwritten by the batch reason — "
        "the cause is more useful than the consequence")
    assert "all-or-nothing" in out["cuda-13.3-24"]["reason"]


def test_configs_with_no_auto_tag_stay_n_a():
    """The stock-* pair has no -auto tag; turning them into holds would both
    miscount and misreport."""
    out = {d["key"]: d for d in _apply(DECISIONS)}
    assert out["stock-22"]["decision"] == "n/a"


def test_an_all_clear_batch_is_untouched():
    clear = [{"key": "a", "decision": "flip", "reason": "QA passed on this exact digest"},
             {"key": "b", "decision": "n/a", "reason": "no auto tag for this config"}]
    assert _apply(clear) == clear, "the rewrite must be a no-op when nothing holds"


def test_the_gate_redraws_on_a_zero_failure_block():
    """A cell that blocks with no failed tests was never really tested."""
    body = QA_GATE.read_text()          # raw: a json dump escapes the shell quoting
    assert "stream_counts.failed" in body, (
        "qa-gate must inspect the failed-test count to tell a bad host from a bad image")
    assert "drawing another host" in body


def test_an_unreachable_instance_is_also_redrawn():
    """A host that comes up and is then unreachable exits 5 (instance_error) with
    no test results. test_template.py's own comment calls 5 "the image's problem",
    which is true for a crash PART WAY THROUGH and false for a box that was never
    reachable — `failed == 0` separates them. Observed live 2026-08-14."""
    body = QA_GATE.read_text()
    assert re.search(r'\[ "\$CODE" -eq 5 \]', body), (
        "instance_error must be eligible for a redraw when nothing was tested")
    guard = _REDRAW_GUARD.search(body)
    assert guard, "the redraw guard no longer matches"
    assert re.search(r'\[\s*"\$\{_failed:?-?[^}]*\}"\s*=\s*"0"\s*\]', guard.group(0)), (
        "the exit-5 redraw must be gated on zero failed tests, exactly like exit 1")


def test_config_error_and_bad_instance_are_NOT_redrawn():
    """4 is our bug — retrying hides it. 3 already walked MAX_LAUNCH_ATTEMPTS
    offers internally, so redrawing repeats work the client has done."""
    guard = _REDRAW_GUARD.search(QA_GATE.read_text())
    assert guard, "the redraw guard no longer matches"
    block = guard.group(0)
    assert '-eq 4' not in block and '-eq 3' not in block, (
        "config_error/bad_instance must not be redrawn")


def test_a_genuine_failure_is_still_never_retried():
    """The redraw must key on zero failed tests, never on exit 1 alone —
    retrying a real red until it passes by luck is how a gate becomes
    decoration, and that rule is unchanged."""
    body = QA_GATE.read_text()
    assert 'CODE" -eq 1' in body and "_failed" in body, (
        "the redraw must be conditional on the failed-test count")
    guard = _REDRAW_GUARD.search(body)
    assert guard, "no exit-1 guard block found"
    block = guard.group(0)
    # Not "does _failed appear" — that survives replacing the condition with
    # `if true`, because the assignment is still in the block. The redraw must be
    # CONDITIONAL on the count being zero.
    assert re.search(r'\[\s*"\$\{_failed:?-?[^}]*\}"\s*=\s*"0"\s*\]', block), (
        "the redraw must be gated on the failed-test count being exactly 0; as "
        "written it would retry a genuine red until it passed by luck")
