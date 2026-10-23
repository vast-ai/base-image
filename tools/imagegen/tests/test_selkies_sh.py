"""Tests for the shared Selkies 2.0 launch library (ADR 0050).

Every desktop image ships the same `opt/supervisor-scripts/utils/selkies.sh`. It
decides, at launch, what the browser can actually use:

  * HTTPS comes from Caddy's own decision (the site proxying to Selkies has a `tls`
    line), because Caddy also requires a usable certificate, which the environment
    cannot tell;
  * the WebSocket client needs a secure context, so plain HTTP with TURN available
    streams over WebRTC instead;
  * the RTC config is always written, so the built-in public relay is never used;
  * TURN uses directive 73478 for TCP and 73479 for UDP (Vast refuses one number
    mapped for both); each maps to its own 1:1 port, and each TURN URL carries its own;
  * coturn never relays to loopback or private ranges, except this container.

The library is sourced from the shipped file, so the text under test is the text
that ships.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LIB = "opt/supervisor-scripts/utils/selkies.sh"
COPIES = [
    REPO / "derivatives/linux-desktop/ROOT" / LIB,
    REPO / "derivatives/pytorch/derivatives/aio-studio/ROOT_BASE" / LIB,
]

TLS_SITE = """{
    admin off
}
:1111 {
    reverse_proxy localhost:11111 {
    }
}
:6100 {
    tls /etc/instance.crt /etc/instance.key
    root * /opt/portal-aio/caddy_manager/public
    reverse_proxy localhost:16100 {
    }
}
"""
# The generator's raw output, before `caddy fmt`: the first site header is indented.
TLS_SITE_UNFORMATTED = """{
    admin off
}
    :6100 {
    tls /etc/instance.crt /etc/instance.key
    reverse_proxy localhost:16100 {
    }
}
"""
TLS_ELSEWHERE = """:1111 {
    tls /etc/instance.crt /etc/instance.key
    reverse_proxy localhost:11111 {
    }
}
:6100 {
    reverse_proxy localhost:16100 {
    }
}
"""


def bash(code: str, tmp_path: Path, env: dict | None = None) -> str:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    for name in ("selkies", "turnserver"):
        p = stub / name
        p.write_text('#!/bin/bash\nprintf "%s\\n" "$0" "$@"\nprintf "LD_PRELOAD=%s SUBFOLDER=%s ENCODER=%s\\n" '
                     '"${LD_PRELOAD-}" "${SUBFOLDER-<unset>}" "${SELKIES_ENCODER-}"\n'
                     'printf "MIC=%s/%s CAM=%s/%s INTERPOSER=%s\\n" "${SELKIES_MICROPHONE_ENABLED-}" '
                     '"${SELKIES_MICROPHONE_ON_START-}" "${SELKIES_WEBCAM_ENABLED-}" '
                     '"${SELKIES_WEBCAM_ON_START-}" "${SELKIES_INTERPOSER-}"\n')
        p.chmod(0o755)
    full = {"PATH": f"{stub}:/usr/bin:/bin", "XDG_RUNTIME_DIR": str(tmp_path / "run")}
    full.update(env or {})
    out = subprocess.run(["bash", "-c", f'. "$1"; {code}', "_", str(COPIES[0])],
                         env=full, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


def plan(tmp_path: Path, caddyfile: str | None, **env) -> dict:
    cf = tmp_path / "Caddyfile"
    if caddyfile is not None:
        cf.write_text(caddyfile)
    out = bash(f'selkies_plan "{cf}"; echo "$SELKIES_PLAN_HTTPS $SELKIES_PLAN_TURN '
               f'$SELKIES_PLAN_MODE $SELKIES_PLAN_DUAL"', tmp_path, env)
    https, turn, mode, dual = out.splitlines()[-1].split()
    return {"https": https, "turn": turn, "mode": mode, "dual": dual}


def test_every_desktop_image_ships_the_same_library():
    bodies = {p: p.read_bytes() for p in COPIES}
    assert len(set(bodies.values())) == 1, "the shared Selkies library drifted between images"


def test_https_is_caddys_tls_decision_for_the_selkies_site(tmp_path):
    """ENABLE_HTTPS=true without a usable certificate still serves HTTP; only the
    generated site says what the browser gets."""
    assert plan(tmp_path, TLS_SITE, ENABLE_HTTPS="false")["https"] == "true"
    assert plan(tmp_path, TLS_SITE_UNFORMATTED)["https"] == "true"
    assert plan(tmp_path, TLS_ELSEWHERE, ENABLE_HTTPS="true")["https"] == "false"
    assert plan(tmp_path, None, ENABLE_HTTPS="true")["https"] == "false"


@pytest.mark.parametrize("caddy,env,mode,dual", [
    (TLS_SITE, {}, "websockets", "false"),
    (TLS_SITE, {"VAST_UDP_PORT_73479": "21361", "PUBLIC_IPADDR": "203.0.113.7"}, "websockets", "true"),
    (TLS_ELSEWHERE, {"VAST_UDP_PORT_73479": "21361", "PUBLIC_IPADDR": "203.0.113.7"}, "webrtc", "false"),
    (TLS_ELSEWHERE, {}, "websockets", "false"),
    (TLS_ELSEWHERE, {"SELKIES_MODE": "websockets", "VAST_TCP_PORT_73478": "21345",
                     "PUBLIC_IPADDR": "203.0.113.7"}, "websockets", "false"),
])
def test_transport_follows_what_the_browser_can_use(tmp_path, caddy, env, mode, dual):
    got = plan(tmp_path, caddy, **env)
    assert (got["mode"], got["dual"]) == (mode, dual)


def rtc(tmp_path: Path, **env) -> dict:
    out = bash('selkies_plan /nonexistent; selkies_write_rtc; cat "$SELKIES_PLAN_RTC"', tmp_path, env)
    return json.loads(out)


def test_rtc_config_without_turn_holds_no_servers(tmp_path):
    """Selkies falls back to a public relay unless this file names the servers."""
    assert rtc(tmp_path) == {"iceServers": []}
    mode = stat.S_IMODE(os.stat(tmp_path / "run/selkies/rtc.json").st_mode)
    assert mode == 0o600


def test_rtc_config_gives_udp_and_tcp_their_own_ports(tmp_path):
    cfg = rtc(tmp_path, VAST_UDP_PORT_73479="21361", VAST_TCP_PORT_73478="21345",
              PUBLIC_IPADDR="203.0.113.7")
    urls = [u for s in cfg["iceServers"] for u in s["urls"]]
    assert "turn:203.0.113.7:21361?transport=udp" in urls
    assert "turn:203.0.113.7:21345?transport=tcp" in urls
    turn = next(s for s in cfg["iceServers"] if "credential" in s)
    secrets = list((tmp_path / "run/selkies").glob("turn-secret.*"))
    assert len(secrets) == 1 and turn["credential"] == secrets[0].read_text().strip()
    assert len(turn["credential"]) >= 24


def test_rtc_config_stays_valid_json_for_any_credential(tmp_path):
    """Selkies reads an unparseable file as absent and falls back to the public relay."""
    cfg = rtc(tmp_path, TURN_HOST="turn.example", TURN_USERNAME='u"1', TURN_PASSWORD='p"a\\ss')
    turn = next(s for s in cfg["iceServers"] if "credential" in s)
    assert (turn["username"], turn["credential"]) == ('u"1', 'p"a\\ss')


def test_bare_turn_server_only_keeps_coturn_off(tmp_path):
    """1.x's TURN_SERVER meant "do not start coturn"; it never named a usable server."""
    got = plan(tmp_path, TLS_ELSEWHERE, TURN_SERVER="turn:relay.example:3478",
               VAST_UDP_PORT_73479="21361", PUBLIC_IPADDR="203.0.113.7")
    assert got["turn"] == "none"


def test_last_boots_caddyfile_does_not_count(tmp_path):
    cf = tmp_path / "Caddyfile"
    cf.write_text(TLS_SITE)
    os.utime(cf, (0, 0))
    out = bash(f'selkies_wait_caddyfile "{cf}" 1 && echo fresh; true', tmp_path)
    assert "fresh" not in out and "assuming plain HTTP" in out


def test_coturn_never_relays_to_loopback_or_private_ranges(tmp_path):
    out = bash("selkies_plan /nonexistent; selkies_coturn", tmp_path,
               {"VAST_UDP_PORT_73479": "21361", "VAST_TCP_PORT_73478": "21345",
                "PUBLIC_IPADDR": "203.0.113.7"})
    assert "turnserver" in out and "-c" in out
    udp = (tmp_path / "run/selkies/turnserver-udp.conf").read_text().splitlines()
    tcp = (tmp_path / "run/selkies/turnserver-tcp.conf").read_text().splitlines()
    assert "listening-port=21361" in udp and "no-tcp" in udp
    assert "listening-port=21345" in tcp and "no-udp" in tcp
    conf = udp
    assert "relay-threads=0" in udp and "relay-threads=0" in tcp
    assert any(l.startswith("pidfile=") and l.endswith("turnserver-udp.pid") for l in udp)
    assert any(l.startswith("pidfile=") and l.endswith("turnserver-tcp.pid") for l in tcp)
    assert not any("allow-loopback-peers" in line for line in conf)
    assert "denied-peer-ip=127.0.0.0-127.255.255.255" in conf
    assert "denied-peer-ip=172.16.0.0-172.31.255.255" in conf
    assert not any(line.startswith("allowed-peer-ip=127.") for line in conf)
    assert not any("user=" in arg for arg in out.splitlines()), "credential on argv"


def test_coturn_stays_down_without_the_turn_directive(tmp_path):
    out = bash("selkies_plan /nonexistent; selkies_coturn", tmp_path)
    assert "not needed" in out and "turnserver" not in out


def test_launch_binds_loopback_preloads_the_shim_and_drops_fallback_names(tmp_path):
    (tmp_path / "VERSION").write_text("v3.1.6\n")
    out = bash(f'SELKIES_NVREACH=/dev/null; SELKIES_PORTAL_VERSION_FILE="{tmp_path}/VERSION"; '
               'selkies_plan /nonexistent; selkies_exec', tmp_path,
               {"SUBFOLDER": "/desktop", "LD_PRELOAD": "/other.so", "SELKIES_ENCODER": "nvh264enc"})
    args = out.splitlines()
    assert "--addr=127.0.0.1" in args and "--port=16100" in args
    assert "--enable-basic-auth=false" in args
    assert any(a.startswith("--rtc-config-json=") for a in args)
    assert "LD_PRELOAD=/dev/null:/other.so SUBFOLDER=<unset> ENCODER=h264enc" in args
    assert any("predates v3.1.7" in a for a in args), "the launch must check the portal version"


STAGE = "etc/vast_boot.d/05-caddy-host-passthrough.sh"
STAGES = [
    REPO / "derivatives/linux-desktop/ROOT" / STAGE,
    REPO / "derivatives/pytorch/derivatives/aio-studio/ROOT_BASE" / STAGE,
]


@pytest.mark.parametrize("template,expected", [
    (None, "16100"),
    ("8188", "8188,16100"),
    ("8188, 16100", "8188, 16100"),
])
def test_every_desktop_image_declares_selkies_for_host_passthrough(template, expected):
    """Without it Caddy rewrites Host and Selkies refuses every browser WebSocket; the
    template's own ports survive."""
    assert len({p.read_bytes() for p in STAGES}) == 1
    env = {"PATH": "/usr/bin:/bin"}
    if template is not None:
        env["CADDY_HOST_PASSTHROUGH"] = template
    out = subprocess.run(["bash", "-c", '. "$1"; printf %s "$CADDY_HOST_PASSTHROUGH"', "_",
                          str(STAGES[0])], env=env, capture_output=True, text=True, check=True)
    assert out.stdout == expected


@pytest.mark.parametrize("version,warns", [
    ("v3.1.6", True), ("3.1.6", True), ("v3.1.7", False), ("v3.2.0", False), ("v3.1.10", False),
])
def test_launch_warns_when_the_portal_predates_host_passthrough(tmp_path, version, warns):
    """An older portal rewrites Host and Selkies refuses every browser WebSocket; the
    first-boot update that brings v3.1.7 can be skipped, so the launcher says so."""
    vf = tmp_path / "VERSION"
    vf.write_text(version + "\n")
    out = bash(f'SELKIES_PORTAL_VERSION_FILE="{vf}"; selkies_check_portal; true', tmp_path)
    assert ("predates v3.1.7" in out) == warns


def test_launch_turns_mic_and_camera_on_demand_and_keeps_interposers_out_of_selkies(tmp_path):
    """Upstream's defaults leave the microphone and camera off, and the side-panel toggle
    alone delivered silence on a live instance. Selkies must know the interposers carry
    gamepads and the camera, but never load them: their hooks block its event loop."""
    out = bash('SELKIES_NVREACH=/dev/null; selkies_plan /nonexistent; selkies_exec', tmp_path,
               {"LD_PRELOAD": "/usr/$LIB/selkies_v4l2_interposer.so:/keep.so"}).splitlines()
    assert "MIC=true/demand CAM=true/demand INTERPOSER=/usr/$LIB/selkies_input_interposer.so" in out
    preload = next(l for l in out if l.startswith("LD_PRELOAD="))
    assert "interposer" not in preload and "/keep.so" in preload
    templated = bash('SELKIES_NVREACH=/dev/null; selkies_plan /nonexistent; selkies_exec', tmp_path,
                     {"SELKIES_MICROPHONE_ON_START": "false"}).splitlines()
    assert any(l.startswith("MIC=true/false ") for l in templated), "a template setting must win"


def test_desktop_session_preloads_the_installed_interposers(tmp_path):
    """The desktop's applications find the client's camera at /dev/video0, and gamepads
    under /dev/input, only through these; `$LIB` must reach the loader unexpanded."""
    libdir = tmp_path / "usrlib/x86_64-linux-gnu"
    libdir.mkdir(parents=True)
    for n in ("selkies_input_interposer", "selkies_v4l2_interposer"):
        (libdir / f"{n}.so").write_text("")
    out = bash(f'SELKIES_INTERPOSER_DIRS="{tmp_path}/usrlib"; selkies_session_preload', tmp_path,
               {"LD_PRELOAD": "/usr/$LIB/selkies_input_interposer.so:/vgl.so"})
    assert out == ("/usr/$LIB/selkies_input_interposer.so:/usr/$LIB/selkies_v4l2_interposer.so"
                   ":/vgl.so")
    assert bash('SELKIES_INTERPOSER_DIRS=/nonexistent; selkies_session_preload', tmp_path,
                {"LD_PRELOAD": "/vgl.so"}) == "/vgl.so"


def test_every_desktop_session_starts_with_the_interposers():
    launches = [
        REPO / "derivatives/linux-desktop/ROOT/opt/supervisor-scripts/kde.sh",
        REPO / "derivatives/pytorch/derivatives/aio-studio/ROOT_BASE/opt/supervisor-scripts/desktop.sh",
    ]
    for f in launches:
        text = f.read_text()
        start = text.index("startplasma-x11")
        assert "selkies_session_preload" in text[max(0, start - 400):start], f


INPUT_STAGE = "etc/vast_boot.d/05-selkies-input-dir.sh"


def test_every_desktop_image_creates_dev_input_for_the_gamepad_interposer():
    stages = [REPO / "derivatives/linux-desktop/ROOT" / INPUT_STAGE,
              REPO / "derivatives/pytorch/derivatives/aio-studio/ROOT_BASE" / INPUT_STAGE]
    assert len({p.read_bytes() for p in stages}) == 1
    assert "mkdir -pm1777 /dev/input" in stages[0].read_text()
