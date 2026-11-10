"""Nothing the launcher shows carries an IP address.

Provider hosts are not ours to publish, and QA output lands in public CI logs. Every
stderr line (launcher messages, test output, streamed engine logs) and the --raw JSON
are redacted; the one exception is the raw JSON's `ssh` field, which the QA-fix loop
reads to log in to a held (--keep) box and which CI never produces.
"""

from __future__ import annotations

import io
import json

import pytest

from test_template import (REDACTED_IP, LogPanel, RedactingStream, redact_ips,
                           redacted_raw_json)


@pytest.mark.parametrize("text,expected", [
    ("Instance 1234567 running (203.0.113.41:40101)",
     f"Instance 1234567 running ({REDACTED_IP}:40101)"),
    ("ssh -p 40122 root@198.51.100.22", f"ssh -p 40122 root@{REDACTED_IP}"),
    ("public 10.0.0.5 and 172.17.0.2", f"public {REDACTED_IP} and {REDACTED_IP}"),
    ("host 2001:db8:400c:c02::65 up", f"host {REDACTED_IP} up"),
    ("[2001:db8::1]:8000", f"[{REDACTED_IP}]:8000"),
])
def test_addresses_are_redacted(text, expected):
    assert redact_ips(text) == expected


@pytest.mark.parametrize("text", [
    "listening on 127.0.0.1:18000",
    "bind 0.0.0.0:8000",
    "INFO 11-09 11:51:05 [api_utils.py:347] started",     # a timestamp is not IPv6
    "vLLM version 0.32.0, CUDA 13.0.3, driver 595.91.07",  # versions are not IPv4
    "::1 loopback",
    "999.1.1.1 is not an address",
    "ratio 1:2:3",
])
def test_non_identifying_text_is_left_alone(text):
    assert redact_ips(text) == text


def test_the_stream_redacts_writes_and_delegates_the_rest():
    buf = io.StringIO()
    s = RedactingStream(buf)
    s.write("from 203.0.113.9\n")
    assert buf.getvalue() == f"from {REDACTED_IP}\n"
    assert s.isatty() is False and s.getvalue() == buf.getvalue()   # delegated


def test_raw_json_is_redacted_except_ssh():
    raw = {"state": "passed", "detail": "box at 203.0.113.9:40000",
           "tests": [{"name": "x", "detail": "peer 198.51.100.4"}],
           "ssh": {"host": "203.0.113.9", "port": 40022}}
    out = json.loads(redacted_raw_json(raw))
    assert out["detail"] == f"box at {REDACTED_IP}:40000"
    assert out["tests"][0]["detail"] == f"peer {REDACTED_IP}"
    assert out["ssh"] == {"host": "203.0.113.9", "port": 40022}
    assert raw["ssh"]["host"] == "203.0.113.9"          # caller's dict untouched


def test_raw_json_without_ssh_has_no_ssh_key():
    assert "ssh" not in json.loads(redacted_raw_json({"state": "passed"}))


# ---------------------------------------------------------------- next to escape sequences

@pytest.mark.parametrize("text,expected", [
    ("\x1b[2m203.0.113.5", f"\x1b[2m{REDACTED_IP}"),                      # the review's case
    ("203.0.113.5\x1b[0m", f"{REDACTED_IP}\x1b[0m"),
    ("\x1b[36mvllm.log\x1b[2m 203.0.113.5:40000 up\x1b[0m",
     f"\x1b[36mvllm.log\x1b[2m {REDACTED_IP}:40000 up\x1b[0m"),
    ("\x1b[1;31m2001:db8::5\x1b[0m", f"\x1b[1;31m{REDACTED_IP}\x1b[0m"),
    ("\r\x1b[K203.0.113.5", f"\r\x1b[K{REDACTED_IP}"),
    ("\x1b]0;box 203.0.113.9\x07", f"\x1b]0;box {REDACTED_IP}\x07"),     # a title is text
])
def test_an_address_beside_an_escape_sequence_is_redacted(text, expected):
    assert redact_ips(text) == expected


def test_escape_sequences_themselves_are_untouched():
    text = "\x1b[1;24r\x1b[24;1H\x1b[s\x1b[2m 127.0.0.1:18000 \x1b[0m\x1b[u"
    assert redact_ips(text) == text


# capsys captures stderr WITHOUT redaction (RedactingStream is installed only by
# main()), so these see what the panel itself writes.

def test_the_log_panel_redacts_a_line_before_decorating_it(capsys):
    panel = LogPanel(enabled=False)
    panel.write_log("203.0.113.5 accepted a connection", src="vllm.log")
    panel.write_log("203.0.113.6 again", src="vllm.log", overwrite=True)
    panel.write_output("203.0.113.7 test output")
    err = capsys.readouterr().err
    assert "203.0.113" not in err and err.count(REDACTED_IP) == 3


def test_the_split_panel_redacts_the_lines_it_redraws(capsys):
    panel = LogPanel(enabled=False)
    panel.active, panel._scroll_end, panel._panel_height = True, 10, 3
    panel.write_log("203.0.113.5 accepted a connection", src="203.0.113.8.log")
    panel.write_output("203.0.113.7 test output")
    assert "203.0.113" not in capsys.readouterr().err
    assert all("203.0.113" not in ln for ln in panel._log_lines)


# ---------------------------------------------------------------- the raw JSON, value by value

def test_an_address_after_an_escaped_newline_in_a_json_string_is_redacted():
    """The review's case: serialized, the value reads `boot\\n203.0.113.5`, and the
    `n` of the escape glued onto the address."""
    out = json.loads(redacted_raw_json({"detail": "boot\n203.0.113.5 up",
                                        "log": "tab\t198.51.100.4"}))
    assert out == {"detail": f"boot\n{REDACTED_IP} up", "log": f"tab\t{REDACTED_IP}"}


def test_raw_json_keys_and_nested_values_are_redacted():
    out = json.loads(redacted_raw_json({"peers": {"203.0.113.5": ["x 2001:db8::9", 7, None]}}))
    assert out == {"peers": {REDACTED_IP: [f"x {REDACTED_IP}", 7, None]}}


def test_only_the_top_level_ssh_field_is_exempt():
    """The exception's scope, pinned: the held box's coordinates at the top level,
    nothing else called ssh."""
    raw = {"ssh": {"host": "203.0.113.9", "cmd": "ssh -p 40022 root@203.0.113.9"},
           "tests": [{"name": "x", "ssh": "root@203.0.113.9"}]}
    out = json.loads(redacted_raw_json(raw))
    assert out["ssh"] == raw["ssh"]
    assert out["tests"][0]["ssh"] == f"root@{REDACTED_IP}"


# ---------------------------------------------------------------- the stream's guarantee

def test_an_address_split_across_writes_is_redacted_within_a_line():
    """The stream is line-buffered: a line is redacted whole, however many writes
    built it."""
    buf = io.StringIO()
    s = RedactingStream(buf)
    s.write("peer 203.0.")
    s.write("113.5")
    assert buf.getvalue() == ""                         # held until the line ends
    s.write(":40000 up\nnext ")
    assert buf.getvalue() == f"peer {REDACTED_IP}:40000 up\n"
    s.flush()
    assert buf.getvalue().endswith("next ")


def test_flush_writes_a_partial_line_redacted():
    """Progress output (`\\r` + no newline) must still appear when flushed."""
    buf = io.StringIO()
    s = RedactingStream(buf)
    s.write("\r\x1b[K  Status: loading on 203.0.113.5")
    s.flush()
    assert buf.getvalue() == f"\r\x1b[K  Status: loading on {REDACTED_IP}"


def test_print_through_the_stream_is_redacted():
    buf = io.StringIO()
    s = RedactingStream(buf)
    print("box", "203.0.113.5", file=s)
    print("dots", end="", file=s, flush=True)
    assert buf.getvalue() == f"box {REDACTED_IP}\ndots"


# ---------------------------------------------------------------- more shapes, and the policy

@pytest.mark.parametrize("text,expected", [
    ("203.0.113.5:8000", f"{REDACTED_IP}:8000"),
    ("http://203.0.113.5:40000/v1/models", f"http://{REDACTED_IP}:40000/v1/models"),
    ("[2001:db8::5]:443", f"[{REDACTED_IP}]:443"),
    ("2001:db8:0:0:0:0:0:5", REDACTED_IP),
    ("fe80::1 link-local", f"{REDACTED_IP} link-local"),
])
def test_more_address_shapes_are_redacted(text, expected):
    assert redact_ips(text) == expected


@pytest.mark.parametrize("text", [
    "torch==2.9.1",                     # three parts
    "nccl 2.27.3.1.0",                  # five parts
    "image v1.2.3.4",                   # glued to a word
])
def test_the_version_false_positive_rule_is_pinned(text):
    """The current rule, pinned (not widened or narrowed here): fewer or more than
    four parts, or glued to a word, is not an address."""
    assert redact_ips(text) == text


@pytest.mark.parametrize("text,expected", [
    ("pkg==1.2.3.4", f"pkg=={REDACTED_IP}"),
    ("cuda-12.9.1.4", f"cuda-{REDACTED_IP}"),          # '-' is not a word character
])
def test_a_bare_four_part_version_is_redacted_fail_safe(text, expected):
    """The known false positive, kept on purpose: a four-part dotted number that is
    a valid public address is redacted, because a hidden version costs less than a
    leaked host."""
    assert redact_ips(text) == expected
