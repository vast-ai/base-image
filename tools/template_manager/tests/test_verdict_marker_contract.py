"""The runner writes verdict markers; the client reads them. Pin the contract.

`runner.sh` prints a line like ``  → PASSED (3s)`` after each test, and
`test_template.py` classifies it by substring into a per-test state. That stream
data is AUTHORITATIVE — it overwrites the per-test states in the results JSON
(the JSON has a write race) — so a marker the client does not recognise does not
produce a wrong state, it produces NO state: the test is dropped from
``tests[]`` entirely, and every downstream consumer, including the required-pass
assertion in qa_verdict.py, then sees a test that simply is not there.

That is exactly backwards from how this system is supposed to fail. It is also
not hypothetical: adding the `timedout` state for ADR 0020 meant changing the
runner's wording from "→ FAILED (timeout after Ns)" to "→ TIMED OUT (after Ns)",
and the client's matcher recognised neither — the first draft of that change
would have made every timed-out test disappear silently.

These tests read BOTH files, so neither side can move alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_template import classify_verdict_marker

RUNNER = Path(__file__).resolve().parents[3] / "ROOT/opt/instance-tools/tests/runner.sh"

# The states the client can produce. A marker MUST map into this set, and the
# set must equal what stream_counts is initialised with, or a += on a missing
# key raises mid-run.
KNOWN_STATES = {"passed", "failed", "skipped", "timedout"}


def runner_markers() -> list[str]:
    """Every verdict line runner.sh can emit, as the client would see it.

    Matches `echo "  → ..."` and returns the text after the arrow, stripped.
    """
    text = RUNNER.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'echo\s+"(\s*→[^"]*)"', text):
        out.append(m.group(1).strip())
    return out


def test_the_runner_actually_emits_markers():
    """Guard the guard: if the regex stops matching, every test below passes
    vacuously over an empty list and this file becomes decoration."""
    markers = runner_markers()
    assert len(markers) >= 4, f"found only {markers!r} — the extraction regex has rotted"


def test_every_marker_the_runner_emits_is_understood_by_the_client():
    """The whole point. An unrecognised marker drops the test silently."""
    unmatched = [m for m in runner_markers() if classify_verdict_marker(m) is None]
    assert not unmatched, (
        "runner.sh emits verdict markers the client cannot classify: "
        f"{unmatched!r}. Such a test is dropped from tests[] rather than "
        "reported, so it cannot fail a required-pass assertion."
    )


def test_every_marker_maps_into_the_known_state_set():
    """stream_counts is a fixed dict; a state outside it raises on `+= 1`."""
    for m in runner_markers():
        state = classify_verdict_marker(m)
        assert state in KNOWN_STATES, f"{m!r} -> {state!r}, which stream_counts has no key for"


def test_the_client_is_ready_for_a_timeout_marker_before_the_runner_emits_one():
    """The runner currently says "→ FAILED (timeout after Ns)", which reads as
    `failed` — correct, since a timeout blocks either way today. The separate
    TIMED OUT wording arrives with the fault-domain classification. The client
    understands it ALREADY, deliberately: this contract's whole failure mode is
    the runner gaining a word the client has not learned, so the client learns it
    first and this asserts the ordering rather than waiting to be surprised."""
    assert classify_verdict_marker("→ TIMED OUT (after 900s)") == "timedout"


@pytest.mark.parametrize("marker,expected", [
    ("→ PASSED (3s)", "passed"),
    ("→ FAILED (exit code 1, 3s)", "failed"),
    ("→ SKIPPED (0s)", "skipped"),
    ("→ TIMED OUT (after 900s)", "timedout"),
])
def test_known_marker_shapes(marker, expected):
    assert classify_verdict_marker(marker) == expected


def test_timed_out_is_not_shadowed_by_the_other_words():
    """"TIMED OUT" is checked first deliberately. If the wording ever became
    e.g. "FAILED: TIMED OUT", a first-match-wins order starting at PASSED/FAILED
    would classify it as a real failure and re-merge the two fault domains."""
    assert classify_verdict_marker("→ FAILED: TIMED OUT (after 900s)") == "timedout"


def test_an_unknown_marker_returns_none_rather_than_guessing():
    """Fail visibly, not plausibly. None is what the contract test above
    detects; a guessed state would be indistinguishable from a real one."""
    assert classify_verdict_marker("→ WOBBLED (3s)") is None


# --- the WIRING, not just the matcher --------------------------------------
#
# Everything above tests classify_verdict_marker in isolation, and all of it
# passed while the loop that calls it was broken. A refactor moved the
# `tests.append(...)` out of the verdict-marker branch into a sibling one, so no
# per-test state was recorded at all. The results JSON's own states have a write
# race and read "running"; this stream data is what overwrites them. With it
# empty, every cell reported state=running, qa_verdict correctly called that
# untrustworthy, and all 71 cells of a live promotion blocked.
#
# A unit test of the matcher cannot see that. These feed real runner output
# through StreamTracker and assert what comes out.

from test_template import StreamTracker

RUN = "─── Running: {} ───"


def feed(lines):
    t = StreamTracker()
    for l in lines:
        t.feed(l)
    return t


def test_a_passing_test_is_recorded_with_its_name():
    """THE regression. If this returns [] the racy JSON states survive and every
    cell blocks on state=running."""
    t = feed([RUN.format("base/60-gpu-cuda"), "→ PASSED (3s)"])
    assert t.tests == [{"name": "base/60-gpu-cuda", "state": "passed"}]
    assert t.counts["passed"] == 1


def test_a_realistic_run_records_every_test():
    t = feed([
        RUN.format("base/60-gpu-cuda"), "→ PASSED (3s)",
        RUN.format("base/61-cuda-compute"), "→ FAILED (exit code 1, 2s)",
        RUN.format("base/62-gpu-libraries"),
        "SKIP: base/62-gpu-libraries: no GPU detected", "→ SKIPPED (0s)",
        RUN.format("pytorch.d/10-torch-core"), "→ TIMED OUT (after 900s)",
    ])
    assert [x["name"] for x in t.tests] == [
        "base/60-gpu-cuda", "base/61-cuda-compute",
        "base/62-gpu-libraries", "pytorch.d/10-torch-core"]
    assert [x["state"] for x in t.tests] == ["passed", "failed", "skipped", "timedout"]
    assert t.counts == {"passed": 1, "failed": 1, "skipped": 1, "timedout": 1}


def test_the_skip_reason_is_captured_without_eating_the_state():
    """The SKIP line arrives BEFORE the verdict marker. Recording the reason must
    not consume the test — that ordering is exactly what the broken refactor got
    wrong."""
    t = feed([RUN.format("base/60-gpu-cuda"),
              "SKIP: base/60-gpu-cuda: no GPU detected",
              "→ SKIPPED (0s)"])
    assert t.skip_reasons == {"base/60-gpu-cuda": "no GPU detected"}
    assert t.tests == [{"name": "base/60-gpu-cuda", "state": "skipped"}], \
        "the SKIP line swallowed the test record"


def test_a_reason_containing_a_colon_survives():
    t = feed(["SKIP: base/x: not ready: waiting on foo"])
    assert t.skip_reasons == {"base/x": "not ready: waiting on foo"}


def test_a_verdict_with_no_preceding_header_is_not_attributed():
    """A stray marker must not attach to the previous test and overwrite it."""
    t = feed([RUN.format("base/60-gpu-cuda"), "→ PASSED (1s)", "→ PASSED (1s)"])
    assert len(t.tests) == 1
    assert t.counts["passed"] == 2, "the count still moves; only attribution stops"


def test_unrelated_output_is_ignored():
    t = feed(["some log line", "  ok: thing", "", "SKIPPED but not a marker"])
    assert t.tests == [] and t.skip_reasons == {}


def test_a_dropped_stream_invalidates_a_partial_header():
    """On reconnect the client clears current_test_name; a verdict arriving
    afterwards must not be attributed to the test that was running before."""
    t = feed([RUN.format("base/60-gpu-cuda")])
    t.current_test_name = None                      # what the reconnect handler does
    t.feed("→ PASSED (3s)")
    assert t.tests == []
