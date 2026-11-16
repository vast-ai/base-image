#!/usr/bin/env python3
"""Report the exemptions a QA cell's in-image tests applied (ADR 0058).

base/29-ca-trust passes over a file it would otherwise fail when a listed exemption
covers it. The cell still passes, so without this step a cell that leaned on an
exemption reads exactly like a clean one. This turns what test_template.py lifted
from the test output (`exemptions` in its --raw payload) into:

  - one GitHub `::warning` for the cell,
  - a table in the run summary,
  - `alert=true` in $GITHUB_OUTPUT when the `worker-bootstrap` exemption was applied:
    the image pre-installs the interpreter the serverless worker needs, so that
    exemption firing means the worker bootstrap stopped using it.

It reports and never decides: the verdict is qa_verdict.py's, and this always exits 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ALERT_ID = "worker-bootstrap"
# The date after which every row of the dated list in images built before 2026-11 fails.
LEGACY_NOTE = ("dated allowlist: this image's copy of the test fails these from "
               "2027-01-01 unless the image is rebuilt")


def load_raw(path: str) -> dict | None:
    """Every payload in the file, merged.

    The gate redraws a host when a cell fails, and each attempt prints one payload line.
    The verdict is the last attempt's, but an exemption applied on ANY attempt is reported:
    an attempt that applied one and then failed elsewhere would otherwise take its warning
    with it. Exemptions are the union (first seen wins); a declared count is kept only
    from the last attempt that stated one for that test.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    merged: dict | None = None
    seen = set()
    for line in lines:
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(loaded, dict):
            continue
        if merged is None:
            merged = {"exemptions": [], "exemptions_declared": {}}
        attempt = [ex for ex in loaded.get("exemptions") or [] if isinstance(ex, dict)]
        for ex in attempt:
            key = (ex.get("test"), ex.get("id"), ex.get("check"), ex.get("path"))
            if key not in seen:
                seen.add(key)
                merged["exemptions"].append(ex)
        declared = loaded.get("exemptions_declared")
        if isinstance(declared, dict) and declared:
            # Counted against this attempt's own lines, so a clean later attempt cannot
            # make an earlier one look incomplete, and the reverse.
            merged["exemptions_declared"] = declared
            merged["_last_declared_lines"] = attempt
    return merged


def summarise(raw: dict | None) -> dict:
    """Pure: payload -> {rows, incomplete, alert, legacy}."""
    rows = []
    for ex in (raw or {}).get("exemptions") or []:
        if isinstance(ex, dict) and ex.get("id") and ex.get("path"):
            rows.append({k: str(ex.get(k, "")) for k in ("test", "id", "check", "path", "reason")}
                        | {"legacy": bool(ex.get("legacy"))})
    # A test that states how many exemptions it applied, and a different number of lines
    # reaching us, means the report is partial (a dropped stream, a changed format).
    incomplete = []
    declared = (raw or {}).get("exemptions_declared") or {}
    if isinstance(declared, dict):
        for test, n in sorted(declared.items()):
            scope = (raw or {}).get("_last_declared_lines")
            pool = rows if scope is None else [e for e in scope if isinstance(e, dict)]
            seen = sum(1 for r in pool if str(r.get("test", "")) == test and not r.get("legacy"))
            if isinstance(n, int) and n != seen:
                incomplete.append(f"{test} states {n}, {seen} line(s) received")
    return {
        "rows": rows,
        "incomplete": incomplete,
        "alert": any(r["id"] == ALERT_ID for r in rows),
        "legacy": any(r["legacy"] for r in rows),
    }


def escape_command(text: str) -> str:
    """Escape a workflow-command message so content from the image cannot end the
    command or start another one."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("`", "'").replace("\r", " ").replace("\n", " ")


def warning_line(cell: str, s: dict) -> str | None:
    if not s["rows"] and not s["incomplete"]:
        return None
    parts = []
    if s["rows"]:
        counts: dict[str, int] = {}
        for r in s["rows"]:
            counts[r["id"]] = counts.get(r["id"], 0) + 1
        ids = ", ".join(f"{i} x{n}" for i, n in sorted(counts.items()))
        parts.append(f"{len(s['rows'])} CA-trust exemption(s) applied ({ids})")
    if s["legacy"]:
        parts.append(LEGACY_NOTE)
    if s["alert"]:
        parts.append("the serverless worker bootstrap installed its own Python instead "
                     "of using the image's")
    if s["incomplete"]:
        parts.append("exemption report incomplete: " + "; ".join(s["incomplete"]))
    return f"::warning title=CA-trust exemptions::{escape_command(cell + ': ' + '; '.join(parts))}"


def summary_markdown(cell: str, s: dict) -> str:
    if not s["rows"] and not s["incomplete"]:
        return ""
    out = [f"### CA-trust exemptions applied — {escape_cell(cell)}", ""]
    if s["rows"]:
        out += ["| Exemption | Check | Path | Reason |", "|---|---|---|---|"]
        for r in s["rows"]:
            ident = r["id"] + (" (dated list)" if r["legacy"] else "")
            out.append("| " + " | ".join(escape_cell(v) for v in
                                         (ident, r["check"], r["path"], r["reason"])) + " |")
        out.append("")
    if s["legacy"]:
        out += [f"Rows marked \"dated list\": {LEGACY_NOTE}.", ""]
    if s["alert"]:
        out += ["`worker-bootstrap` applied: the serverless worker bootstrap installed its "
                "own Python at boot instead of using the one the image ships.", ""]
    for note in s["incomplete"]:
        out += [f"Exemption report incomplete: {escape_cell(note)}.", ""]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", metavar="FILE", required=True,
                    help="file holding test_template.py --raw output")
    ap.add_argument("--cell", required=True, help="how to name this cell, e.g. repo:tag")
    ap.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    ap.add_argument("--step-summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    args = ap.parse_args(argv)

    s = summarise(load_raw(args.raw))
    line = warning_line(args.cell, s)
    if line:
        print(line)
    else:
        print("no CA-trust exemptions reported")
    md = summary_markdown(args.cell, s)
    if md and args.step_summary:
        with open(args.step_summary, "a", encoding="utf-8") as fh:
            fh.write(md)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"alert={'true' if s['alert'] else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
