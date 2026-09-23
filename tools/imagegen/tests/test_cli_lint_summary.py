"""The lint summary must report warnings it is not printing.

THE defect: `lint --all` filtered non-errors out of `findings` BEFORE counting them, so
the summary line said nothing about them. That matters because `--all` is the invocation
CLAUDE.md prescribes and the one every "baseline CLEAN" evidence paste is quoted from.
L097's design point is that a value it cannot read (`${{ matrix.require_tests }}`) is
reported as a WARN naming itself "rather than dropped silently -- the failure mode L087
condemns" -- and that report printed nowhere. The silent drop had simply moved out of the
check and into the presentation layer, which is the harder place to notice it.

Nothing tested cmd_lint's counting or its output, in a change whose whole thesis is that
an unproven claim is how the original bug shipped. These tests are that proof.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

import imagegen.linter as L
from imagegen.cli import cmd_lint
from imagegen.discover import find_repo_root

REPO = find_repo_root(Path(__file__).resolve().parent)


def _run(capsys, *, warn: bool, findings):
    """cmd_lint over the real repo, with the repo-level checks replaced by `findings`."""
    args = argparse.Namespace(repo=str(REPO), all=True, image=None, warn=warn)
    original = L.lint_repo
    L.lint_repo = lambda repo: list(findings)
    try:
        import imagegen.cli as cli
        prior, cli.lint_repo = cli.lint_repo, L.lint_repo
        try:
            rc = cmd_lint(args)
        finally:
            cli.lint_repo = prior
    finally:
        L.lint_repo = original
    return rc, capsys.readouterr().out


def _summary(out: str) -> str:
    """The counts line -- `N images | N errors | ...` -- not the verdict line after it."""
    for line in out.strip().splitlines():
        if "images |" in line:
            return line
    raise AssertionError(f"no counts line in output:\n{out}")


def _warn(msg="requires tests from a runtime expression, which this check cannot read"):
    return L.Finding("L097", L.WARN, "-", ".github/workflows/promote-pytorch.yml", msg)


def test_a_warning_is_counted_even_when_it_is_not_printed(capsys):
    """The defect, directly: without --warn the warning is filtered from the output,
    and the count must still say it exists."""
    rc, out = _run(capsys, warn=False, findings=[_warn()])
    assert rc == 0
    summary = _summary(out)
    assert "warnings" in summary, f"the summary hid a warning entirely: {summary!r}"
    assert "0 warnings" not in summary


def test_the_summary_says_how_to_see_them(capsys):
    """A count with no way to act on it is barely better than silence: 34 -> 35 is not
    a signal anyone reads. The line has to name the flag that lists them."""
    _rc, out = _run(capsys, warn=False, findings=[_warn()])
    assert "--warn" in _summary(out)


def test_the_warning_body_is_still_suppressed_without_the_flag(capsys):
    """Counting them is not printing them -- `--all` stays quiet enough to read."""
    _rc, out = _run(capsys, warn=False, findings=[_warn("UNIQUE-WARN-BODY")])
    assert "UNIQUE-WARN-BODY" not in out


def test_with_the_flag_the_warning_itself_appears(capsys):
    _rc, out = _run(capsys, warn=True, findings=[_warn("UNIQUE-WARN-BODY")])
    assert "UNIQUE-WARN-BODY" in out
    assert "--warn to list" not in out, "the hint is pointless once they are listed"


def test_the_count_is_accurate_not_merely_present(capsys):
    """A number that does not track reality is the same defect wearing a number. The
    repo's own image-level warnings are already in this count, so this adds one
    repo-level warning and asserts the total moves by exactly one."""
    _rc, before_out = _run(capsys, warn=False, findings=[])
    _rc, after_out = _run(capsys, warn=False, findings=[_warn()])
    before = int(re.search(r"(\d+) warnings", _summary(before_out)).group(1))
    after = int(re.search(r"(\d+) warnings", _summary(after_out)).group(1))
    assert after == before + 1, f"count did not follow the findings: {before} -> {after}"


def test_an_error_is_still_what_sets_the_exit_code(capsys):
    """Warnings are advisory. Counting them must not make them blocking, or the next
    unreadable value turns a promotion red."""
    err = L.Finding("L097", L.ERROR, "-", ".github/workflows/build-x.yml", "boom")
    rc_warn_only, _ = _run(capsys, warn=False, findings=[_warn()])
    rc_error, _ = _run(capsys, warn=False, findings=[err])
    assert rc_warn_only == 0
    assert rc_error != 0
