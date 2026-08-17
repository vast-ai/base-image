#!/usr/bin/env python3
"""Turn a junit report from the cloudflared contract into ONE of three states.

WHY THIS EXISTS. The contract's live tests skip when Cloudflare is unreachable or
has rate-limited us — deliberately, because a Cloudflare outage should not red a
base build. But `pytest` then exits 0 with nothing proven, and an exit code cannot
tell "the shipped binary answers the way the portal drives it" apart from "we
never asked". Measured, under a real per-IP rate limit:

    3 passed, 3 skipped in 1.04s
    === exit code: 0

The three that passed introspect the portal's own argv — a statement about the
module in this checkout, not about the binary. So the job reported success having
verified nothing, and the notification rendered green. This script is what makes
that state say its own name.

    verified    every live test ran and passed
    unverified  no live test failed, but at least one did not run
    broken      a live test failed -> the contract moved

Exit codes: 0 verified, 0 unverified (the caller decides), 1 broken. The state is
written to stdout and to $GITHUB_OUTPUT as `state`, so the caller never has to
infer it from an exit code — which is the mistake this file exists to correct.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET


def classify(path: str) -> tuple[str, list[str]]:
    root = ET.parse(path).getroot()
    # junit nests <testsuite> under <testsuites> in xunit2; accept both shapes.
    cases = root.iter("testcase")
    live_ran, live_skipped, live_failed, notes = 0, [], [], []
    for c in cases:
        name = f"{c.get('classname', '')}::{c.get('name', '')}"
        # Read the MARKER, carried into the report by an autouse fixture in the
        # test file — not a list of test names restated here. A parallel list
        # would be one rename away from silently classifying a live test as an
        # offline one, i.e. from reporting `verified` having verified nothing:
        # the precise failure this file exists to prevent.
        is_live = any(p.get("name") == "live"
                      for p in c.iter("property"))
        if not is_live:
            continue
        skipped = c.find("skipped")
        failure = c.find("failure") if c.find("failure") is not None else c.find("error")
        if failure is not None:
            live_failed.append(name)
            notes.append(f"FAILED {name}: {(failure.get('message') or '')[:200]}")
        elif skipped is not None:
            live_skipped.append(name)
            notes.append(f"skipped {name}: {(skipped.get('message') or '')[:200]}")
        else:
            live_ran += 1

    if live_failed:
        return "broken", notes
    if live_skipped or live_ran == 0:
        return "unverified", notes or ["no live test was collected at all"]
    return "verified", [f"{live_ran} live assertions executed against the shipped binary"]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: classify_contract_run.py <junit.xml>", file=sys.stderr)
        return 2
    try:
        state, notes = classify(sys.argv[1])
    except (OSError, ET.ParseError) as e:
        # No parseable report means the run died before reporting. That is not a
        # clean "unverified" — treat an unreadable report as broken rather than
        # letting a crashed run inherit the benign state.
        state, notes = "broken", [f"could not read the junit report: {e}"]

    for n in notes:
        print(n)
    print(f"state={state}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"state={state}\n")
    return 1 if state == "broken" else 0


if __name__ == "__main__":
    raise SystemExit(main())
