#!/usr/bin/env python3
"""Decide a QA cell's verdict from test_template.py's exit code + --raw payload.

ADR 0019. This is the CI-side half of the fail-not-skip contract: the runner's
own `INSTANCE_TEST_REQUIRE_PASS` gate (ADR 0019 W2) enforces the same rule inside
the instance, but it depends on the QA template actually carrying that env var.
Asserting it again out here means a template that loses the variable cannot
produce a passing verdict — the two layers fail differently, which is the point.

The verdict vocabulary is deliberately three-valued and does NOT decide policy:

  pass          the artifact is good
  block         do not promote — a real failure, or a result we cannot trust
  inconclusive  we learned nothing about the artifact (thin market, infra)

What to *do* with `inconclusive` (retry, hold, soft-pass) belongs to the caller;
ADR 0005 settled that a gating path holds and only an unattended schedule may
soft-pass. Keeping that out of here is why this stays a pure function.

Bash owns the retry loop and the exit; Python owns the decision (repo convention:
bash for plumbing, Python for structured/tested logic).

Usage:
    qa_verdict.py --exit-code N --raw FILE [--require-tests "a b,c"] [--github-output FILE]

Exits 0 always — the caller maps verdict -> job outcome, so a verdict is never
confused with this tool's own failure.

Output contract: the verdict is written to the file named by --github-output (or
$GITHUB_OUTPUT) as `verdict=` / `reason=` key-value lines, and a human-readable
line goes to stdout. Callers must NOT `eval` the stdout: a reason legitimately
contains spaces, semicolons and parentheses ("3 required test(s) passed"), which
under `bash -e` is a syntax error that fails the step on a PASSING cell. Writing
the machine-readable form to a file keeps arbitrary text out of the shell.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

PASS = "pass"
BLOCK = "block"
INCONCLUSIVE = "inconclusive"

# Mirrors test_template.py's EXIT_* constants. Duplicated deliberately: this tool
# reads a *finished* process's exit code and must not import a module that talks
# to the Vast API at import time.
EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_NO_OFFERS = 2
EXIT_BAD_INSTANCE = 3
EXIT_CONFIG_ERROR = 4
EXIT_INSTANCE_ERROR = 5
EXIT_INTERRUPTED = 130


def parse_required(spec: str | None) -> list[str]:
    """Accept space- and/or comma-separated names (templates are hand-written)."""
    if not spec:
        return []
    return [n for n in spec.replace(",", " ").split() if n]


def classify(exit_code: int, raw: dict | None, required: list[str]) -> tuple[str, str]:
    """Map (exit code, raw payload, required tests) -> (verdict, reason).

    Pure: no I/O, no environment, no policy. Unit-tested against real payload shapes.
    """
    raw = raw or {}
    state = raw.get("state", "?")

    # Infra outcomes first: they say nothing about the artifact, so the required-test
    # check below would be meaningless (there are no test results to inspect).
    if exit_code in (EXIT_NO_OFFERS, EXIT_BAD_INSTANCE):
        return INCONCLUSIVE, f"{state}: no usable box was obtained, so the image was not tested"
    if exit_code == EXIT_CONFIG_ERROR:
        return BLOCK, f"{state}: CI/template error — fix the harness, do not promote"
    if exit_code == EXIT_INTERRUPTED:
        return BLOCK, f"{state}: run was interrupted; a cancellation is not a pass"
    if exit_code in (EXIT_FAILED, EXIT_INSTANCE_ERROR):
        return BLOCK, f"{state}: a real test failure or the instance died mid-test"
    if exit_code != EXIT_PASSED:
        return BLOCK, f"unexpected exit code {exit_code} (state={state})"

    # exit 0 from here on.
    if state != "passed":
        return BLOCK, f"exit 0 but state={state!r} — inconsistent, not trustworthy"

    # ADR 0005 cond 2: a pass inferred from a post-test poll, without the runner's
    # own result event, is not trustworthy enough to auto-promote on.
    if not raw.get("got_result_event"):
        return BLOCK, "passed without a result event (ADR 0005 cond 2)"

    if required:
        states = {t.get("name"): t.get("state") for t in raw.get("tests") or []}
        missing = [n for n in required if n not in states]
        not_passed = [f"{n}={states[n]}" for n in required
                      if n in states and states[n] != "passed"]
        if missing or not_passed:
            bits = []
            if missing:
                # Absent from the payload entirely: the test never ran, or the image
                # does not ship it. Either way the claim it backs is unsupported.
                bits.append("absent: " + ", ".join(sorted(missing)))
            if not_passed:
                bits.append("did not pass: " + ", ".join(sorted(not_passed)))
            return BLOCK, "required tests unsatisfied (" + "; ".join(bits) + ")"

    return PASS, f"{state} with a result event" + (
        f"; {len(required)} required test(s) passed" if required else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exit-code", type=int, required=True,
                    help="exit code from test_template.py")
    ap.add_argument("--raw", metavar="FILE",
                    help="file holding test_template.py --raw output")
    ap.add_argument("--require-tests", default="",
                    help="space/comma-separated test names that must have passed")
    ap.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"),
                    help="file to append verdict=/reason= to (default: $GITHUB_OUTPUT)")
    args = ap.parse_args(argv)

    raw = None
    parse_note = ""
    if args.raw:
        try:
            with open(args.raw, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            text = ""
            parse_note = f" (unreadable --raw: {e})"
        # The client prints the verdict as the FINAL single-line JSON object;
        # narration goes to stderr, but be robust to any bleed by taking the last
        # complete {...} line rather than parsing the whole file. A whole-file parse
        # once turned a real PASS into a BLOCK.
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    raw = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

    if raw is None and args.exit_code == EXIT_PASSED:
        # Exit 0 with no readable verdict payload is not a pass we can stand behind.
        verdict, reason = BLOCK, "exit 0 but no parseable --raw verdict" + parse_note
    else:
        verdict, reason = classify(args.exit_code, raw, parse_required(args.require_tests))

    # Human-readable to stdout (safe to log, never parsed by the shell).
    print(f"{verdict}: {reason}")

    # Machine-readable to a file. A reason contains spaces, ';' and '()', so it
    # must never reach the shell as text to evaluate.
    if args.github_output:
        # A multiline-safe heredoc, per GitHub's own output format — a reason with
        # a newline would otherwise inject arbitrary keys.
        delim = "qa_verdict_EOF"
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"verdict={verdict}\n")
            fh.write(f"reason<<{delim}\n{reason}\n{delim}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
