"""log-tee's clean copy carries no escape sequence, wherever the reads fall.

Every supervised service logs through /usr/local/bin/log-tee, which keeps colour in
the portal's copy and strips it from the clean copy and from stdout. It reads its
input in pieces, and a piece can end inside an escape sequence. Cleaning each piece
on its own let the two halves through, and one after the other they are the
sequence again: a colour code in /var/log/<name>.log, which base/70-logging rejects.
Where the reads fall depends on timing, so it showed on some machines only.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from imagegen.discover import find_repo_root

LOG_TEE = find_repo_root(Path(__file__).resolve()) / "ROOT/usr/local/bin/log-tee"


@pytest.fixture(scope="module")
def log_tee():
    loader = importlib.machinery.SourceFileLoader("log_tee_under_test", str(LOG_TEE))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def _fed(log_tee, pieces):
    stream = log_tee.CleanStream()
    return "".join(stream.feed(p) for p in pieces) + stream.flush()


# Every construct the cleaning treats specially, and the orders that matter: a colour
# code between \r and \n, a code directly after \r, \r\r\n, and a 24-bit foreground
# and background in one code (37 characters of parameters).
TEXT = ("\x1b[32mINFO\x1b[0m load \x1b[1;38;5;208mweights\x1b[0m 40%\r\x1b[?25l100%\r\x1b[0m\n"
        "a\r\r\nb\r\x1b[2Kc\x1b[0m\r\n\x1b[38;2;255;128;100;48;2;100;128;255mdone\x1b[0m\n")
CLEAN = "INFO load weights 40%\n100%\na\n\nb\nc\ndone\n"


def test_any_way_of_cutting_the_input_gives_the_same_clean_text(log_tee):
    assert log_tee._clean_for_stdout(TEXT) == CLEAN
    assert _fed(log_tee, [TEXT]) == CLEAN
    assert _fed(log_tee, list(TEXT)) == CLEAN              # one character per read
    for i in range(1, len(TEXT)):                          # every two cuts
        for j in range(i, len(TEXT)):
            pieces = [TEXT[:i], TEXT[i:j], TEXT[j:]]
            assert _fed(log_tee, pieces) == CLEAN, f"cut at {i} and {j}: {pieces!r}"


def test_text_that_only_looks_like_a_sequence_is_released_without_its_escape(log_tee):
    # Past the cap it is not held for ever, and what is released carries no ESC: a
    # final letter arriving later must not complete a sequence in the clean copy.
    stream = log_tee.CleanStream()
    never_ends = "\x1b[" + "1;" * 200
    out = stream.feed(never_ends)
    assert out != "" and "\x1b" not in out
    assert "\x1b" not in stream.feed("m rest\n") + stream.flush()
    # An ESC that begins nothing this tool recognises does not survive either.
    assert "\x1b" not in _fed(log_tee, ["a\x1b[1~b\n"])


def test_the_end_of_input_ends_a_held_line_and_drops_a_sequence_cut_short(log_tee):
    assert _fed(log_tee, ["progress\r"]) == "progress\n"
    for cut_short in ("\x1b", "\x1b[", "\x1b[38;2;25"):
        assert _fed(log_tee, ["done\n" + cut_short]) == "done\n"


def test_a_sequence_across_the_read_size_stays_out_of_the_clean_log(tmp_path):
    """The tool end to end: a write larger than one read, with a colour code lying
    across the boundary. The portal copy keeps its colour; the clean copy and stdout
    have none."""
    portal = tmp_path / "portal" / "svc.log"
    portal.parent.mkdir()
    data = b"x" * (8192 - 3) + b"\x1b[32mINFO\x1b[0m tail\n"
    done = subprocess.run([sys.executable, str(LOG_TEE), str(portal)], input=data,
                          capture_output=True, timeout=30)
    assert done.returncode == 0, done.stderr
    clean = (tmp_path / "svc.log").read_bytes()
    assert b"\x1b" not in clean and clean.endswith(b"INFO tail\n")
    assert done.stdout == clean
    assert portal.read_bytes() == data
