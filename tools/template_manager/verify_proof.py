#!/usr/bin/env python3
"""Verify a launcher proof directory (proof.py, ADR 0059 D10).

    verify_proof.py <proof-dir> [<proof-dir> ...]

Recomputes the sha256 of every file the proof lists and of the record itself, and
checks the schema version is one this tool knows. Exit 0 when every proof verifies,
1 on a mismatch, a missing or extra file, or an unreadable proof, 2 on an unknown
schema version.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

try:
    import proof as _proof
except ImportError:                     # pragma: no cover - run from elsewhere
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import proof as _proof

KNOWN_SCHEMAS = (1,)
EXIT_OK, EXIT_MISMATCH, EXIT_UNKNOWN_SCHEMA = 0, 1, 2


def verify(path):
    """(exit code, list of problems) for one proof directory."""
    try:
        with open(os.path.join(path, _proof.PROOF_FILE), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError) as e:
        return EXIT_MISMATCH, [f"cannot read {_proof.PROOF_FILE}: {e}"]
    if not isinstance(rec, dict):
        return EXIT_MISMATCH, ["proof.json is not an object"]
    if rec.get("schema_version") not in KNOWN_SCHEMAS:
        return EXIT_UNKNOWN_SCHEMA, [f"unknown schema_version {rec.get('schema_version')!r} "
                                     f"(known: {', '.join(map(str, KNOWN_SCHEMAS))})"]
    integrity = rec.pop("integrity", None)
    if not isinstance(integrity, dict) or integrity.get("algorithm") != "sha256":
        return EXIT_MISMATCH, ["no sha256 integrity block"]
    problems = []
    if hashlib.sha256(_proof.canonical(rec)).hexdigest() != integrity.get("record_sha256"):
        problems.append("the record does not match record_sha256")
    listed = integrity.get("files") or {}
    present = set()
    for root, _, names in os.walk(path):
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), path).replace(os.sep, "/")
            if rel != _proof.PROOF_FILE:
                present.add(rel)
    for rel in sorted(set(listed) - present):
        problems.append(f"missing file: {rel}")
    for rel in sorted(present - set(listed)):
        problems.append(f"file not in the proof: {rel}")
    for rel in sorted(set(listed) & present):
        if _proof.sha256_file(os.path.join(path, rel)) != listed[rel]:
            problems.append(f"hash mismatch: {rel}")
    return (EXIT_MISMATCH if problems else EXIT_OK), problems


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        return EXIT_MISMATCH
    worst = EXIT_OK
    for path in args:
        code, problems = verify(path)
        print(f"{'ok' if code == EXIT_OK else 'FAILED'}: {path}")
        for p in problems:
            print(f"  {p}")
        worst = max(worst, code)
    return worst


if __name__ == "__main__":
    sys.exit(main())
