"""Minimal instruction-aware Dockerfile parser.

Enough structure to stop the linter from being fooled by keywords in comments,
across line-continuations, in heredoc bodies, or in the wrong position. NOT a
full Docker parser.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

_HEREDOC = re.compile(r"<<(-?)\s*([\"']?)(\w+)\2")


@dataclass
class Instruction:
    cmd: str   # upper-cased, e.g. RUN, FROM, LABEL, COPY
    value: str # remainder, continuations joined, full-line comments dropped
    line: int  # 1-based line where the instruction starts
    exec: str = ""  # executed-shell text: excludes heredoc bodies used as command stdin


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
        # consume heredoc bodies so their lines aren't parsed as instructions.
        # plain <<EOF terminates only at a column-0 EOF; <<-EOF allows leading tabs.
        cmd_line = buf  # the command portion, before any heredoc body
        has_heredoc = bool(_HEREDOC.search(cmd_line))
        pending = [(m.group(3), bool(m.group(1))) for m in _HEREDOC.finditer(cmd_line)]
        while i < n and pending:
            body = lines[i]
            i += 1
            buf += "\n" + body
            for idx, (term, dashed) in enumerate(pending):
                if (body.strip() == term) if dashed else (body.rstrip() == term):
                    pending.pop(idx)
                    break
        m = re.match(r"\s*(\w+)\s*(.*)", buf, re.S)
        if not m:
            continue
        cmd, value = m.group(1).upper(), m.group(2)
        # executed-shell text: a heredoc body is executed only when the heredoc IS the
        # script (`RUN <<EOF`); if a command precedes it (`RUN cat <<EOF`) the body is
        # that command's stdin (data) and must be excluded from regex checks.
        exec_text = value
        if has_heredoc:
            rem = _HEREDOC.sub("", cmd_line)
            rem = re.sub(r"^\s*\w+\s*", "", rem, count=1)   # drop the instruction keyword
            rem = re.sub(r"[><|&;].*$", "", rem, flags=re.S)  # drop redirections/operators
            if rem.strip():  # a real command precedes the heredoc -> body is data
                cl = re.match(r"\s*\w+\s*(.*)", cmd_line, re.S)
                exec_text = cl.group(1) if cl else cmd_line
        out.append(Instruction(cmd, value, start, exec_text))
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


def _expand(content: str, defaults: dict[str, str | None]) -> str:
    # handle ${VAR}, ${VAR:-def}, ${VAR-def}, ${VAR:=def}. The ARG value wins when
    # set (the inline `:-` default is dead once the build-arg/ARG is set).
    m = re.match(r"(\w+):?[-=](.*)$", content, re.S)
    if m:
        val = defaults.get(m.group(1))
        return val if val else m.group(2)
    val = defaults.get(content)
    return val if val else "${" + content + "}"


def resolve(ref: str, defaults: dict[str, str | None]) -> str:
    """Substitute ${VAR}/${VAR:-def}/$VAR in a FROM ref with known ARG defaults."""
    def sub(m: re.Match) -> str:
        if m.group(1) is not None:
            return _expand(m.group(1), defaults)
        name = m.group(2)
        val = defaults.get(name)
        return val if val else m.group(0)
    return re.sub(r"\$\{([^}]*)\}|\$(\w+)", sub, ref)


def parse_ref(ref: str) -> tuple[str | None, str, str | None]:
    """Split an image ref into (registry, repo, tag). registry=None means Docker Hub."""
    ref = ref.strip().split("@", 1)[0]
    parts = ref.split("/")
    registry: str | None = None
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, parts = parts[0], parts[1:]
    last = parts[-1]
    tag: str | None = None
    if ":" in last:
        name, tag = last.rsplit(":", 1)
        parts = parts[:-1] + [name]
    return registry, "/".join(parts), tag


def code_text(instrs: list[Instruction]) -> str:
    """Comment-free, executed-shell reconstruction for substring/regex checks
    (heredoc data bodies excluded — see Instruction.exec)."""
    return "\n".join(f"{i.cmd} {i.exec}" for i in instrs)


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
