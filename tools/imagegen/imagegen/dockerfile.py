"""Minimal instruction-aware Dockerfile parser.

Enough structure to stop the linter from being fooled by keywords in comments,
across line-continuations, in heredoc bodies, or in the wrong position. NOT a
full Docker parser.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

_HEREDOC = re.compile(r"<<-?\s*([\"']?)(\w+)\1")


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
        # join line continuations, skipping comment-only continuation lines
        while buf.rstrip().endswith("\\"):
            buf = buf.rstrip()[:-1]
            while i < n and lines[i].strip().startswith("#"):
                i += 1  # Docker drops comment lines inside a continuation
            if i >= n:
                break
            buf += "\n" + lines[i]
            i += 1
        # consume heredoc bodies so their lines aren't parsed as instructions
        pending = [m.group(2) for m in _HEREDOC.finditer(buf)]
        while i < n and pending:
            body = lines[i]
            i += 1
            buf += "\n" + body
            if body.strip() in pending:
                pending.remove(body.strip())
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


def arg_defaults(instrs: list[Instruction]) -> dict[str, str | None]:
    """Map ARG name -> default value (None if declared with no default)."""
    d: dict[str, str | None] = {}
    for ins in instrs:
        if ins.cmd != "ARG":
            continue
        m = re.match(r"(\w+)=(.+)", ins.value.strip(), re.S)
        if m:
            d[m.group(1)] = m.group(2).strip().strip('"').strip("'")
        else:
            d.setdefault(ins.value.strip().split()[0] if ins.value.strip() else "", None)
    return d


def resolve(ref: str, defaults: dict[str, str | None]) -> str:
    """Substitute ${VAR}/$VAR in a FROM ref with known ARG defaults."""
    def sub(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        val = defaults.get(name)
        return val if val else m.group(0)
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", sub, ref)


def code_text(instrs: list[Instruction]) -> str:
    """Comment-free reconstruction for substring/regex checks."""
    return "\n".join(f"{i.cmd} {i.value}" for i in instrs)


def ini_sections(text: str) -> dict[str, dict[str, str]]:
    """Parse a supervisor .conf into {section: {key: value}} (last value wins)."""
    sections: dict[str, dict[str, str]] = {}
    cur: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and "]" in s:  # tolerate trailing comment after ]
            cur = s[1:s.index("]")]
            sections.setdefault(cur, {})
        elif cur and "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            sections[cur][k.strip()] = v.strip()
    return sections
