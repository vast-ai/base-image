"""Split a multi-entry environment variable value into its entries (ADR 0053).

Entries are separated by ``,`` or ``;``. A comma does not separate when it is
inside ``[...]`` (pip extras: ``transformers[torch,sentencepiece]``) or when what
follows, after any spaces, is a version operator (``<``, ``>``, ``=``, ``!=``, ``~=``;
pip ranges: ``torch>=2.4,<2.6``).
Write a literal comma in a URL as ``%2C``.

Both separators are accepted everywhere because Vast drops any template variable
whose value contains ``;``, so a ``;``-only list never reaches the instance.

Run as a module to split for shell callers: one entry per NUL-terminated record.
"""

from __future__ import annotations

import sys

_VERSION_OPERATOR_STARTS = ("<", ">", "=", "!=", "~=")


def split_entries(value: str) -> list[str]:
    """Return the stripped, non-empty entries of *value*."""
    entries: list[str] = []
    current: list[str] = []
    depth = 0
    for i, ch in enumerate(value):
        if ch == "[":
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
        elif ch == ";" or (ch == "," and depth == 0 and not _before_operator(value, i + 1)):
            entries.append("".join(current))
            current = []
            continue
        current.append(ch)
    entries.append("".join(current))
    return [e.strip() for e in entries if e.strip()]


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
