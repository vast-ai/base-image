"""Minimal instruction-aware Dockerfile parser.

Enough structure to stop the linter from being fooled by keywords in comments,
across line-continuations, or in the wrong position. NOT a full Docker parser.
"""
from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class Instruction:
    cmd: str   # upper-cased, e.g. RUN, FROM, LABEL, COPY
    value: str # remainder, continuations joined, full-line comments dropped
    line: int  # 1-based line where the instruction starts


def parse(text: str) -> list[Instruction]:
    lines = text.splitlines()
    out: list[Instruction] = []
    i, n = 0, len(lines)
    while i < n:
        start = i + 1
        raw = lines[i]
        i += 1
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        buf = raw
        # join line continuations; skip comment-only continuation lines
        while buf.rstrip().endswith("\\") and i < n:
            buf = buf.rstrip()[:-1] + "\n" + lines[i]
            i += 1
        m = re.match(r"\s*(\w+)\s*(.*)", buf, re.S)
        if not m:
            continue
        out.append(Instruction(m.group(1).upper(), m.group(2), start))
    return out


def stages(instrs: list[Instruction]) -> list[tuple[str, str | None]]:
    """Return [(image_ref, alias|None)] for each FROM, in order."""
    out: list[tuple[str, str | None]] = []
    for ins in instrs:
        if ins.cmd != "FROM":
            continue
        parts = ins.value.split()
        if not parts:
            continue
        alias = parts[2] if len(parts) >= 3 and parts[1].upper() == "AS" else None
        out.append((parts[0], alias))
    return out


def code_text(instrs: list[Instruction]) -> str:
    """Comment-free reconstruction for substring/regex checks."""
    return "\n".join(f"{i.cmd} {i.value}" for i in instrs)


def ini_sections(text: str) -> dict[str, dict[str, str]]:
    """Parse a supervisor .conf into {section: {key: value}} (last value wins)."""
    sections: dict[str, dict[str, str]] = {}
    cur: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
            sections.setdefault(cur, {})
        elif cur and "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            sections[cur][k.strip()] = v.strip()
    return sections
