#!/usr/bin/env python3
"""The copy of a cell's --raw verdict payload that is uploaded as qa-evidence.

    public_evidence.py <raw-in> <raw-out>

The uploaded evidence artifact is public. The payload test_template.py prints
carries `machine_id` (which host the cell ran on) and, with --keep, `ssh`
coordinates; the qa-gate loop needs machine_id IN THE JOB to keep a redraw off a
failed host, so the job's own /tmp/qa-raw.json is left whole and only this copy is
stripped. Every JSON line is kept with those keys (and any token-named key) removed
wherever they occur; the instance id stays (it ties the evidence to the run). Lines
that are not a JSON object are dropped: the readers (the promote workflows,
qa_verdict.py) read only the JSON lines. A missing input writes nothing.
"""

from __future__ import annotations

import json
import os
import re
import sys

HOST_KEYS = frozenset({"machine_id", "host_id", "ssh", "public_ipaddr", "ssh_host", "ssh_port"})
_TOKEN_KEY = re.compile(r"token|secret|password|api_key", re.I)


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items()
                if k not in HOST_KEYS and not _TOKEN_KEY.search(str(k))}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def public_lines(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict):
            out.append(json.dumps(strip(doc)))
    return out


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    src, dst = args
    try:
        with open(src, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return 0                                   # nothing to publish
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write("".join(ln + "\n" for ln in public_lines(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
