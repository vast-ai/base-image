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


def test_a_split_anywhere_gives_the_same_clean_text(log_tee):
    text = "\x1b[32mINFO\x1b[0m loading \x1b[1;38;5;208mweights\x1b[0m 40%\r\x1b[?25l100%\r\ndone\n"
    whole = _fed(log_tee, [text])
    assert whole == "INFO loading weights 40%\n100%\ndone\n"
    for cut in range(1, len(text)):
        assert _fed(log_tee, [text[:cut], text[cut:]]) == whole, f"split at {cut}: {text[:cut]!r}"
    assert _fed(log_tee, list(text)) == whole          # one character per read


def test_text_that_only_looks_like_a_sequence_is_not_held_for_ever(log_tee):
    stream = log_tee.CleanStream()
    never_ends = "\x1b[" + "1;" * 40
    assert stream.feed(never_ends) != ""


def test_the_end_of_input_writes_what_was_held(log_tee):
    assert _fed(log_tee, ["progress\r"]) == "progress\n"
    assert _fed(log_tee, ["cut short \x1b[3"]).startswith("cut short ")
    assert len(_fed(log_tee, ["cut short \x1b[3"])) > len("cut short ")


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
