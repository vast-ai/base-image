"""Split a multi-entry environment variable value into its entries (ADR 0053).

A value containing ``;`` is split on ``;`` only, exactly as before this module
existed, so every existing ``;`` list (onstart scripts export them) keeps its meaning,
commas inside entries included.

A value without ``;`` is split on ``,``. Vast drops any template variable whose value
contains ``;``, so a template can only use commas. A comma does not separate when it
is inside ``[...]`` (pip extras: ``transformers[torch,sentencepiece]``) or when what
follows, after any spaces, is a version operator (``<``, ``>``, ``=``, ``!=``, ``~=``;
pip ranges: ``torch>=2.4,<2.6``). Write a literal comma in a URL as ``%2C``.

Run directly (by file path) to split for shell callers: one entry per NUL-terminated record.
"""

from __future__ import annotations

import sys

_VERSION_OPERATOR_STARTS = ("<", ">", "=", "!=", "~=")


def split_entries(value: str) -> list[str]:
    """Return the stripped, non-empty entries of *value*."""
    parts = value.split(";") if ";" in value else _split(value, brackets=True)
    return [e.strip() for e in parts if e.strip()]


def _split(value: str, brackets: bool) -> list[str]:
    entries: list[str] = []
    current: list[str] = []
    depth = 0
    opened = 0  # where in `current` the outermost "[" that is still open began

    def finish() -> None:
        text = "".join(current)
        if not depth:
            entries.append(text)
            return
        # A "[" never closed is a typo or a raw bracket in a URL, not pip extras: split
        # what followed it normally, keeping the text before it (closed extras
        # included) on the first piece.
        parts = _split(text[opened:], brackets=False)
        parts[0] = text[:opened] + parts[0]
        entries.extend(parts)

    for i, ch in enumerate(value):
        if brackets and ch == "[":
            if not depth:
                opened = len(current)
            depth += 1
        elif brackets and ch == "]" and depth:
            depth -= 1
        elif ch == "," and depth == 0 and not _before_operator(value, i + 1):
            finish()
            current, depth = [], 0
            continue
        current.append(ch)
    finish()
    return entries


def _before_operator(value: str, start: int) -> bool:
    rest = value[start:].lstrip()
    return rest.startswith(_VERSION_OPERATOR_STARTS)


def as_list(value) -> list:
    """A manifest list field: a list stays as written, a string is split into entries."""
    if isinstance(value, str):
        return split_entries(value)
    return value


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    for entry in split_entries(text):
        sys.stdout.write(entry + "\0")
