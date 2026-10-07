"""Tests for the multi-entry env var splitter (ADR 0053)."""

import subprocess
import sys
from pathlib import Path

import pytest

from provisioner.envlist import split_entries


@pytest.mark.parametrize("value,expected", [
    # Vast drops any template variable containing ";", so "," must separate too.
    ("ffmpeg,libgl1", ["ffmpeg", "libgl1"]),
    ("ffmpeg;libgl1", ["ffmpeg", "libgl1"]),
    ("a, b;c ,,", ["a", "b", "c"]),
    # A pip range keeps its comma, with or without a space after it.
    ("torch>=2.4,<2.6,numpy", ["torch>=2.4,<2.6", "numpy"]),
    ("numpy!=1.0, >=0.9,scipy~=1.11,==1.11.4", ["numpy!=1.0, >=0.9", "scipy~=1.11,==1.11.4"]),
    # "~" and "!" alone are not operators: a home path or a command still splits.
    ("chmod +x a,~/bin/run", ["chmod +x a", "~/bin/run"]),
    # pip extras keep theirs.
    ("transformers[torch,sentencepiece]>=4.40,accelerate",
     ["transformers[torch,sentencepiece]>=4.40", "accelerate"]),
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
