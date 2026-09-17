"""Tests for derivatives/.../wan2gp/ROOT/etc/vast_boot.d/05-wan2gp-env.sh (ADR 0042).

Wan2GP rejects any WebSocket or POST whose Origin differs from scheme://Host as
the app sees it, so behind Caddy the port must be in CADDY_HEADER_UP_LOCALHOST.
The template may already set that variable for other apps; the boot stage adds
our port without dropping theirs.

The trap this table exists for is "true". caddy_config_manager.py reads "true"
as "every port" and anything else as a port list, so appending to "true" would
produce "true,7860" — a list — and silently remove the rewrite from every other
port. The other rows pin how the list is read: the same split-and-strip Caddy
uses, so a port Caddy already matches is never appended twice.

The script is sourced as the boot runner sources it, so the text under test is
the text that ships.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[3]
          / "derivatives/pytorch/derivatives/wan2gp/ROOT/etc/vast_boot.d/05-wan2gp-env.sh")

UNSET = object()


def run(value, port=None) -> str:
    env = {"PATH": "/usr/bin:/bin"}
    if value is not UNSET:
        env["CADDY_HEADER_UP_LOCALHOST"] = value
    if port is not None:
        env["WAN2GP_PORT"] = port
    out = subprocess.run(
        ["bash", "-c", '. "$1"; printf "%s|%s" "${CADDY_HEADER_UP_LOCALHOST-<unset>}" '
                       '"${wan2gp_port-}${wan2gp_listed-}${wan2gp_ports-}${wan2gp_p-}"',
         "_", str(SCRIPT)],
        env=env, capture_output=True, text=True, check=True)
    result, leaked = out.stdout.split("|")
    assert leaked == "", "the sourced stage leaked its working variables into the boot shell"
    return result


@pytest.mark.parametrize("value,port,expected", [
    (UNSET, None, "7860"),                 # not set: create it with our port
    ("", None, "7860"),                    # set but empty: same as unset
    (UNSET, "9000", "9000"),               # the port follows WAN2GP_PORT
    ("8080", None, "8080,7860"),           # set without our port: append
    ("8080,8384", None, "8080,8384,7860"),
    ("8080,", None, "8080,7860"),          # no empty entry from a trailing comma
    ("false", None, "false,7860"),         # "false" is just a list to Caddy
    ("7860", None, "7860"),                # already listed: unchanged
    ("8080, 7860", None, "8080, 7860"),    # Caddy strips spaces, so this is listed
    ("17860", None, "17860,7860"),         # a substring is not a match
    ("8080,7860", "9000", "8080,7860,9000"),
    ("true", None, "true"),                # every port already: must not narrow it
    ("TRUE", None, "TRUE"),
    ("True", "9000", "True"),
])
def test_port_is_ensured(value, port, expected):
    assert run(value, port) == expected


def test_caddy_generator_still_parses_it_this_way():
    """The table above assumes Caddy's parsing. If the generator changes, this
    stage has to change with it."""
    src = (Path(__file__).resolve().parents[3]
           / "portal-aio/caddy_manager/caddy_config_manager.py").read_text(encoding="utf-8")
    assert "header_up_localhost.split(',')" in src
    assert 'header_up_localhost.lower() == "true"' in src
    assert "str(internal_port) in ports_list" in src
