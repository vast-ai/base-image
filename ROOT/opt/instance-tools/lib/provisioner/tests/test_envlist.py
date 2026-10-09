"""Tests for the multi-entry env var splitter (ADR 0053)."""

import subprocess
import sys
from pathlib import Path

import pytest

from provisioner.envlist import split_entries


@pytest.mark.parametrize("value,expected", [
    # Vast drops any template variable containing ";", so a value without one splits on ",".
    ("ffmpeg,libgl1", ["ffmpeg", "libgl1"]),
    ("ffmpeg;libgl1", ["ffmpeg", "libgl1"]),
    ("a, b,,c ,", ["a", "b", "c"]),
    # A pip range keeps its comma, with or without a space after it.
    ("torch>=2.4,<2.6,numpy", ["torch>=2.4,<2.6", "numpy"]),
    ("numpy!=1.0, >=0.9,scipy~=1.11,==1.11.4", ["numpy!=1.0, >=0.9", "scipy~=1.11,==1.11.4"]),
    ("a>1,!=1.5,b", ["a>1,!=1.5", "b"]),
    ("a>1, ~=1.4,b", ["a>1, ~=1.4", "b"]),
    # "~" and "!" alone are not operators: a home path or a command still splits.
    ("chmod +x a,~/bin/run", ["chmod +x a", "~/bin/run"]),
    # pip extras keep theirs.
    ("transformers[torch,sentencepiece]>=4.40,accelerate",
     ["transformers[torch,sentencepiece]>=4.40", "accelerate"]),
    # An unclosed "[" does not swallow the rest of the value.
    ("x[a,b,https://h/c|/m/", ["x[a", "b", "https://h/c|/m/"]),
    ("x[a,b,c", ["x[a", "b", "c"]),
    ("pkg[a,b]x[c,d", ["pkg[a,b]x[c", "d"]),
    # A URL's own comma is written %2C; url|dest fields are untouched.
    ("https://h/a%2Cb.bin|/m/,https://h/c.bin|/m/c.bin", ["https://h/a%2Cb.bin|/m/", "https://h/c.bin|/m/c.bin"]),
])
def test_split_entries(value, expected):
    assert split_entries(value) == expected


def test_module_emits_nul_records_for_shell_callers():
    """sd-forge's bash parser runs the file directly and reads NUL-terminated records."""
    script = Path(__file__).resolve().parents[1] / "envlist.py"
    out = subprocess.run([sys.executable, str(script), "a>=1,<2, b"], capture_output=True, check=True).stdout
    assert out == b"a>=1,<2\0b\0"


def _before_this_change(value: str) -> list[str]:
    """How every caller split a list before ADR 0053: on ";" only."""
    return [e.strip() for e in value.split(";") if e.strip()]


@pytest.mark.parametrize("value", [
    "transformers>=4.0;accelerate;torch",
    "ffmpeg;libgl1;htop",
    "https://h/a.safetensors|/m/a.safetensors;https://civitai.com/api/download/models/1?type=Model&format=SafeTensor|/m/",
    "https://github.com/o/a|/w/a|v2.0;https://github.com/o/b",
    # Entries with a bare comma, which a comma-splitting rule would have cut apart.
    "echo a,b > /tmp/x;touch /tmp/y",
    "awk -F, '{print $1}' /tmp/in > /tmp/out;touch /tmp/done",
    "mkdir -p /w/{a,b};touch /w/a/x",
    "https://example.com/f?ids=1,2|/m/f;https://example.com/g|/m/g",
    "a,b;c",
    "x[a,b;c,d",
    "only-one-entry;",
])
def test_a_value_with_a_semicolon_splits_exactly_as_before(value):
    """Live templates export ";" lists from onstart, which the platform's filter never
    sees, so every one must keep its meaning, commas inside entries included."""
    assert split_entries(value) == _before_this_change(value)


def test_semicolon_values_match_the_old_split_for_any_text():
    import random
    rng = random.Random(53)
    alphabet = "ab ,;[]<>=!~|:/.#$'\"\t"
    for _ in range(2000):
        value = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 30)))
        if ";" in value:
            assert split_entries(value) == _before_this_change(value), repr(value)
