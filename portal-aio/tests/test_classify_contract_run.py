"""Tests for the thing that decides whether the contract proved anything.

`classify_contract_run.py` exists because pytest's exit code cannot tell
"verified" from "never asked", and the CI job now renders ✅/⚠️/❌ from its
verdict. That makes it the load-bearing part: if it says `verified` when nothing
ran, the gate is decoration again with an extra step. The repo's own rule is that
an untested check does not count, and the anti-silent-skip mechanism is exactly
where that rule has been broken before.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classify_contract_run as C  # noqa: E402


def _report(tmp_path, cases: list[tuple[str, str, bool]]) -> str:
    """Build a junit report. Each case is (name, outcome, is_live)."""
    body = []
    for name, outcome, live in cases:
        props = '<properties><property name="live" value="1"/></properties>' if live else ""
        inner = {"pass": "", "skip": '<skipped message="rate-limited"/>',
                 "fail": '<failure message="the contract moved"/>',
                 "error": '<error message="died"/>'}[outcome]
        body.append(f'<testcase classname="t" name="{name}">{props}{inner}</testcase>')
    p = tmp_path / "j.xml"
    p.write_text(f'<testsuites><testsuite name="p">{"".join(body)}</testsuite></testsuites>')
    return str(p)


def test_all_live_assertions_ran_is_verified(tmp_path):
    r = _report(tmp_path, [("argv", "pass", False), ("live_a", "pass", True),
                           ("live_b", "pass", True)])
    assert C.classify(r)[0] == "verified"


def test_a_skipped_live_assertion_is_UNVERIFIED_not_verified(tmp_path):
    """The measured rate-limit case: `3 passed, 3 skipped`, exit 0. The passes
    are argv introspection and say nothing about the binary."""
    r = _report(tmp_path, [("argv", "pass", False), ("live_a", "skip", True),
                           ("live_b", "skip", True)])
    assert C.classify(r)[0] == "unverified"


def test_one_skip_among_passes_still_downgrades(tmp_path):
    """Partial evidence is not evidence. If any live assertion did not execute,
    the run did not verify the contract."""
    r = _report(tmp_path, [("live_a", "pass", True), ("live_b", "skip", True)])
    assert C.classify(r)[0] == "unverified"


@pytest.mark.parametrize("outcome", ["fail", "error"])
def test_a_failing_live_assertion_is_broken(tmp_path, outcome):
    r = _report(tmp_path, [("live_a", "pass", True), ("live_b", outcome, True)])
    assert C.classify(r)[0] == "broken"


def test_broken_outranks_unverified(tmp_path):
    """A run with one break and one skip is broken. Reporting the milder state
    would let a real contract break ship behind a ⚠️."""
    r = _report(tmp_path, [("live_a", "skip", True), ("live_b", "fail", True)])
    assert C.classify(r)[0] == "broken"


def test_a_report_with_NO_live_tests_is_unverified(tmp_path):
    """Deselection (`-m "not live"`), a collection error, or a rename that loses
    the marker all land here. None of them verified anything."""
    r = _report(tmp_path, [("argv", "pass", False)])
    assert C.classify(r)[0] == "unverified"


def test_offline_tests_alone_cannot_produce_verified(tmp_path):
    """The exact shape of the bug: the argv tests pass without touching the
    binary, so a report full of them must not read as a verified contract."""
    r = _report(tmp_path, [("argv1", "pass", False), ("argv2", "pass", False),
                           ("argv3", "pass", False)])
    assert C.classify(r)[0] != "verified"


def test_liveness_comes_from_the_MARKER_not_the_test_name(tmp_path):
    """A test named like a live one but unmarked must not count as live
    evidence, and a live-marked test with an unrelated name must. Keying on
    names would make a rename silently downgrade the gate to nothing."""
    misleading = _report(tmp_path, [("test_a_live_quick_tunnel_thing", "pass", False)])
    assert C.classify(misleading)[0] == "unverified", (
        "an unmarked test must not be counted as live evidence")
    renamed = _report(tmp_path, [("something_entirely_different", "pass", True)])
    assert C.classify(renamed)[0] == "verified"


def test_an_unreadable_report_is_broken_not_benign(tmp_path):
    """A run that died before reporting has not verified anything, and must not
    inherit the state that lets the build go green."""
    missing = tmp_path / "nope.xml"
    out = subprocess.run([sys.executable, str(Path(C.__file__)), str(missing)],
                         capture_output=True, text=True)
    assert "state=broken" in out.stdout
    assert out.returncode == 1

    garbage = tmp_path / "g.xml"
    garbage.write_text("<not-xml")
    out = subprocess.run([sys.executable, str(Path(C.__file__)), str(garbage)],
                         capture_output=True, text=True)
    assert "state=broken" in out.stdout
    assert out.returncode == 1


def test_the_exit_code_fails_only_on_broken(tmp_path):
    """unverified must NOT fail the step — a Cloudflare outage reds the build is
    how the gate gets switched off. It is the notification's job to say the
    contract was not verified, not the build's job to fail over it."""
    for outcome, expect in [("pass", 0), ("skip", 0), ("fail", 1)]:
        r = _report(tmp_path, [("live_a", outcome, True)])
        out = subprocess.run([sys.executable, str(Path(C.__file__)), r],
                             capture_output=True, text=True)
        assert out.returncode == expect, f"{outcome} -> rc {out.returncode}"
