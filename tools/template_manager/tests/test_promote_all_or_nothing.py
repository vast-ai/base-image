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

_REDRAW_GUARD = re.compile(r'if \[ "\$CODE" -ne 0 \].*?\n            fi', re.S)

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


def test_the_gate_redraws_on_ANY_failure_not_just_a_zero_failure_block():
    """SUPERSEDES the zero-failure redraw rule (2026-08-19).

    The old rule redrew only when NO test had failed — written for a GPU-less
    box, where the required-pass gate fires on skips. It could not help when a
    bad HOST makes a real test FAIL, which is what actually happened: the
    2026-08-18 pytorch gate blocked six cells, and every one investigated passed
    on other hardware — an NCCL segfault inside libcuda on machine 35974 (two
    controls on the identical driver build passed), two multi-GPU collectives
    timeouts whose test then passed 10/10 on the same GPU model, driver and image
    digest, and three supervisord boot races.

    Reproducibility is the discriminator, not the symptom.
    """
    body = QA_GATE.read_text()
    assert '[ "$CODE" -ne 0 ] && [ "$CODE" -ne 4 ]' in body, (
        "the redraw must fire on any non-zero exit except config_error")
    assert _REDRAW_GUARD.search(body), "no redraw guard block found"


def test_config_error_is_the_one_exit_never_redrawn():
    """config_error is OUR bug; retrying it hides it.

    bad_instance is no longer excluded. The client's own launch attempts are
    about reaching a RUNNING box, which says nothing about whether that box can
    pass the suite — so exhausting them is not a reason to stop drawing.
    """
    body = QA_GATE.read_text()
    assert '"$CODE" -ne 4' in body, "config_error must never be redrawn"


def test_a_flaky_image_defect_can_now_pass_by_luck_AND_IS_RECORDED():
    """The accepted cost of redrawing a red, stated rather than hidden.

    A DETERMINISTIC image defect still blocks: it fails every draw, and the
    attempt count bounds the loop. A FLAKY one — failing on some boxes, passing
    on others — can now escape, because one passing redraw ends the loop. That is
    a genuine weakening of the previous rule, taken deliberately: the alternative
    cost six blocked cells on hosts that were simply bad.

    What makes it survivable is that every failed attempt is RECORDED with the
    machine it ran on, so an image failing across many DIFFERENT machines shows
    as a pattern instead of being laundered into a green tick.
    """
    body = QA_GATE.read_text()
    assert "SUSPECT-HOST" in body and ".machine_id" in body, (
        "redrawing a red is only defensible if each failed attempt names the "
        "host it failed on; without that a flaky image defect is invisible")
    assert "qa-suspect-hosts.txt" in body, (
        "every failed attempt must be kept, not just the last — one machine "
        "failing is a bad host, five different machines failing is the image")
