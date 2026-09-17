"""Tests for the 05-wan2gp-env.sh boot stage (ADR 0042).

Two images ship it: the standalone wan2gp image (Wan2GP on 7860) and aio-studio
(Wan2GP on 17861). The script is the same apart from the default port, and every
row below runs against both.

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

REPO = Path(__file__).resolve().parents[3]
STAGE = "ROOT/etc/vast_boot.d/05-wan2gp-env.sh"

# image dir -> the port its supervisor script launches Wan2GP on by default
IMAGES = {
    "derivatives/pytorch/derivatives/wan2gp": "7860",
    "derivatives/pytorch/derivatives/aio-studio": "17861",
}

UNSET = object()


def run(script, value, port=None) -> str:
    env = {"PATH": "/usr/bin:/bin"}
    if value is not UNSET:
        env["CADDY_HEADER_UP_LOCALHOST"] = value
    if port is not None:
        env["WAN2GP_PORT"] = port
    out = subprocess.run(
        ["bash", "-c", '. "$1"; printf "%s|%s" "${CADDY_HEADER_UP_LOCALHOST-<unset>}" '
                       '"${wan2gp_port-}${wan2gp_listed-}${wan2gp_ports-}${wan2gp_p-}"',
         "_", str(script)],
        env=env, capture_output=True, text=True, check=True)
    result, leaked = out.stdout.split("|")
    assert leaked == "", "the sourced stage leaked its working variables into the boot shell"
    return result


# D is replaced by the image's default port.
@pytest.mark.parametrize("image", IMAGES)
@pytest.mark.parametrize("value,port,expected", [
    (UNSET, None, "D"),                    # not set: create it with our port
    ("", None, "D"),                       # set but empty: same as unset
    (UNSET, "9000", "9000"),               # the port follows WAN2GP_PORT
    ("8080", None, "8080,D"),              # set without our port: append
    ("8080,3000", None, "8080,3000,D"),
    ("8080,", None, "8080,D"),             # no empty entry from a trailing comma
    ("false", None, "false,D"),            # "false" is just a list to Caddy
    ("D", None, "D"),                      # already listed: unchanged
    ("8080, D", None, "8080, D"),          # Caddy strips spaces, so this is listed
    ("1D", None, "1D,D"),                  # a superstring is not a match
    ("8080,D", "9000", "8080,D,9000"),
    ("true", None, "true"),                # every port already: must not narrow it
    ("TRUE", None, "TRUE"),
    ("True", "9000", "True"),
])
def test_port_is_ensured(image, value, port, expected):
    d = IMAGES[image]
    sub = lambda v: v if v in (UNSET, None) else v.replace("D", d)
    assert run(REPO / image / STAGE, sub(value), port) == sub(expected)


@pytest.mark.parametrize("image,port", IMAGES.items())
def test_default_port_is_the_one_the_app_is_launched_on(image, port):
    """The stage's default must be the launcher's default, and the image's own
    PORTAL_CONFIG default (if it has one) must route to it. aio-studio shipped a
    default Wan2GP entry of 7861:7861 while launching on 17861."""
    launcher = (REPO / image / "ROOT/opt/supervisor-scripts/wan2gp.sh").read_text(encoding="utf-8")
    assert f"${{WAN2GP_PORT:-{port}}}" in launcher
    assert f'wan2gp_port="${{WAN2GP_PORT:-{port}}}"' in (REPO / image / STAGE).read_text(encoding="utf-8")
    for env in (REPO / image / "ROOT/etc/vast_boot.d").glob("05-*-env.sh"):
        for entry in env.read_text(encoding="utf-8").split("|"):
            if entry.endswith(":Wan2GP"):
                assert entry.split(":")[2] == port, f"{env.name}: {entry}"


def test_the_copies_differ_only_in_the_default_port():
    texts = [(REPO / i / STAGE).read_text(encoding="utf-8").split("wan2gp_port=", 1)[1]
             for i in IMAGES]
    norm = [t.replace(f"WAN2GP_PORT:-{p}", "WAN2GP_PORT:-X") for t, p in zip(texts, IMAGES.values())]
    assert norm[0] == norm[1]


def test_caddy_generator_still_parses_it_this_way():
    """The table above assumes Caddy's parsing. If the generator changes, this
    stage has to change with it."""
    src = (REPO
           / "portal-aio/caddy_manager/caddy_config_manager.py").read_text(encoding="utf-8")
    assert "header_up_localhost.split(',')" in src
    assert 'header_up_localhost.lower() == "true"' in src
    assert "str(internal_port) in ports_list" in src
