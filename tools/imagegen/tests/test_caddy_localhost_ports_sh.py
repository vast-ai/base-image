"""Tests for the 05-caddy-localhost-ports.sh boot stage (ADR 0043, from ADR 0042).

Some apps reject a WebSocket or POST whose Origin differs from scheme://Host as
they see it, which behind Caddy is always localhost. An image whose app does this
lists the app's internal port in its copy of the stage, and the stage adds each
listed port to CADDY_HEADER_UP_LOCALHOST at boot, keeping the template's entries.

Every image ships the SAME body, delimited by marker comments; only the
`caddy_localhost_ports=(...)` line above it differs. That is what this file pins:

  * the bodies are identical, and every image that ships the stage is covered;
  * each declared port is the port the image actually serves that app on;
  * the merge itself, including the trap this exists for: Caddy reads "true" as
    "every port" and anything else as a port list, so appending to "true" would
    produce "true,7860" and silently drop the rewrite from every other app.

The scripts are sourced as the boot runner sources them, so the text under test
is the text that ships.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
STAGE = "ROOT/etc/vast_boot.d/05-caddy-localhost-ports.sh"
D = "derivatives/pytorch/derivatives"

# image dir -> the exact caddy_localhost_ports line it must declare
IMAGES = {
    f"{D}/wan2gp": 'caddy_localhost_ports=("${WAN2GP_PORT:-7860}")',
    f"{D}/ace-step": "caddy_localhost_ports=(3000)",
    f"{D}/aio-studio": 'caddy_localhost_ports=("${WAN2GP_PORT:-17861}" 3000)',
}

_BODY = re.compile(r"^# ---- shared body: identical in every image \(ADR 0043\) ----$\n"
                   r"(.*?)^# ---- end shared body ----$", re.S | re.M)
_WORK_VARS = ("caddy_localhost_ports", "caddy_localhost_port", "caddy_localhost_listed",
              "caddy_localhost_entries", "caddy_localhost_entry")
UNSET = object()


def script(image: str) -> Path:
    return REPO / image / STAGE


def run(image: str, value=UNSET, wan2gp_port: str | None = None) -> str:
    env = {"PATH": "/usr/bin:/bin"}
    if value is not UNSET:
        env["CADDY_HEADER_UP_LOCALHOST"] = value
    if wan2gp_port is not None:
        env["WAN2GP_PORT"] = wan2gp_port
    leak = "".join(f"${{{v}-}}" for v in _WORK_VARS)
    out = subprocess.run(
        ["bash", "-c", f'. "$1"; printf "%s|%s" "${{CADDY_HEADER_UP_LOCALHOST-<unset>}}" "{leak}"',
         "_", str(script(image))],
        env=env, capture_output=True, text=True, check=True)
    result, leaked = out.stdout.split("|")
    assert leaked == "", "the sourced stage leaked its working variables into the boot shell"
    return result


# --- the convention -----------------------------------------------------------

def test_every_image_shipping_the_stage_is_covered():
    shipped = {str(p.relative_to(REPO)).removesuffix("/" + STAGE)
               for p in REPO.glob(f"**/{STAGE}") if ".git" not in p.parts and "pcl" not in p.parts}
    assert shipped == set(IMAGES)


def test_the_body_is_identical_in_every_image():
    bodies = {}
    for image in IMAGES:
        m = _BODY.search(script(image).read_text(encoding="utf-8"))
        assert m, f"{image}: the shared-body markers are gone"
        bodies[image] = m.group(1)
    assert len(set(bodies.values())) == 1, "the shared body has drifted between images"


@pytest.mark.parametrize("image,line", IMAGES.items())
def test_each_image_declares_its_ports(image, line):
    lines = [l for l in script(image).read_text(encoding="utf-8").splitlines()
             if l.startswith("caddy_localhost_ports=")]
    assert lines == [line]


@pytest.mark.parametrize("image,port", [(f"{D}/wan2gp", "7860"), (f"{D}/aio-studio", "17861")])
def test_wan2gp_port_is_the_one_it_is_launched_on(image, port):
    launcher = (REPO / image / "ROOT/opt/supervisor-scripts/wan2gp.sh").read_text(encoding="utf-8")
    assert f"--server-port ${{WAN2GP_PORT:-{port}}}" in launcher


@pytest.mark.parametrize("image", [f"{D}/ace-step", f"{D}/aio-studio"])
def test_ace_step_port_is_asserted_at_build(image):
    """The UI port is hardcoded upstream, so the build must fail if it moves."""
    dockerfile = (REPO / image / "Dockerfile").read_text(encoding="utf-8")
    assert "port:[[:space:]]*3000," in dockerfile


def test_aio_studio_default_routes_reach_the_declared_ports():
    """The image-default PORTAL_CONFIG must route to the ports the stage lists.
    aio-studio shipped a Wan2GP entry of 7861:7861 while launching on 17861."""
    env = (REPO / D / "aio-studio/ROOT/etc/vast_boot.d/05-aio-studio-env.sh").read_text(encoding="utf-8")
    internal = {e.split(":", 4)[4].split('"')[0]: e.split(":")[2]
                for e in re.findall(r"localhost:\d+:\d+:[^|:]*:[^|\"]+", env)}
    assert internal["Wan2GP"] == "17861"
    assert internal["ACE Step"] == "3000"


# --- the merge ----------------------------------------------------------------

# P is replaced by the image's single port.
SINGLE = {f"{D}/wan2gp": "7860", f"{D}/ace-step": "3000"}


@pytest.mark.parametrize("image", SINGLE)
@pytest.mark.parametrize("value,expected", [
    (UNSET, "P"),                     # not set: create it with our port
    ("", "P"),                        # set but empty: same as unset
    ("8080", "8080,P"),               # set without our port: append
    ("8080,8384", "8080,8384,P"),
    ("8080,", "8080,P"),              # no empty entry from a trailing comma
    ("false", "false,P"),             # "false" is just a list to Caddy
    ("P", "P"),                       # already listed: unchanged
    ("8080, P", "8080, P"),           # Caddy strips spaces, so this is listed
    ("1P", "1P,P"),                   # a superstring is not a match
    ("true", "true"),                 # every port already: must not narrow it
    ("TRUE", "TRUE"),
])
def test_single_port(image, value, expected):
    p = SINGLE[image]
    sub = lambda v: v if v is UNSET else v.replace("P", p)
    assert run(image, sub(value)) == sub(expected)


@pytest.mark.parametrize("value,wan2gp_port,expected", [
    (UNSET, None, "17861,3000"),
    ("3000", None, "3000,17861"),              # today's aio-studio template
    ("3000,17861", None, "3000,17861"),
    ("8080", None, "8080,17861,3000"),
    (UNSET, "9000", "9000,3000"),              # follows WAN2GP_PORT
    ("17861", "9000", "17861,9000,3000"),
    (UNSET, "3000", "3000"),                   # the same port twice is listed once
    ("True", "9000", "True"),
])
def test_multiple_ports(value, wan2gp_port, expected):
    assert run(f"{D}/aio-studio", value, wan2gp_port) == expected


def test_wan2gp_port_override():
    assert run(f"{D}/wan2gp", UNSET, "9000") == "9000"
    assert run(f"{D}/wan2gp", "7860", "9000") == "7860,9000"


def test_caddy_generator_still_parses_it_this_way():
    """The table above assumes Caddy's parsing. If the generator changes, the stage
    has to change with it."""
    src = (REPO / "portal-aio/caddy_manager/caddy_config_manager.py").read_text(encoding="utf-8")
    assert "header_up_localhost.split(',')" in src
    assert 'header_up_localhost.lower() == "true"' in src
    assert "str(internal_port) in ports_list" in src
