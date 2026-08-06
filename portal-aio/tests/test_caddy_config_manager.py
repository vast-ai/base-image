"""Unit tests for the Caddy generator's "backing service not ready" placeholder.

Invariant (ADR 0017, option B): the not-ready placeholder is served as HTTP 200
**only for Cloudflare-tunnelled requests** (they carry a ``Cf-Ray`` header) so the
tunnel does not swap our loader for its "Host is down" page; every other path keeps
the real 5xx, so a direct prober / Vast / monitoring still sees "not ready". Both
paths carry the ``X-Portal-Placeholder`` marker, and 502.html's poll reloads only
when that marker is ABSENT and the status is not a 5xx.

These tests pin BOTH halves that must agree — the generator emits the marker, and
the poll reads that exact name with the exact reload condition — so a rename, a
boolean inversion, or a deleted reload all turn CI red (see ADR 0017 finding 1).

Run from the portal-aio directory (or repo root) with pytest; the caddy_manager
module is put on sys.path below so it imports without installation.
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PORTAL_AIO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_PORTAL_AIO, "caddy_manager"))

import caddy_config_manager as ccm  # noqa: E402

PUBLIC_502 = os.path.join(_PORTAL_AIO, "caddy_manager", "public", "502.html")


# --- invariant predicate (shared by real-render and mutation tests) --------- #

def _status_var(block):
    m = re.search(r"status\s+\{vars\.(\w+)\}", block)
    return m.group(1) if m else None


def _placeholder_contract_ok(block):
    """True iff the not-ready placeholder honours ADR-0017 option B:
    - the served status comes from a request var,
    - its DIRECT (unmatched) default is a 5xx (probers see "not ready"),
    - its Cloudflare-tunnel override (matched var) is a non-5xx (passes the tunnel),
    - the X-Portal-Placeholder marker is present.
    """
    var = _status_var(block)
    if var is None or "X-Portal-Placeholder" not in block:
        return False
    direct = re.search(rf"vars\s+{var}\s+(\d{{3}})", block)          # `vars <var> NNN`
    tunnel = re.search(rf"vars\s+@\w+\s+{var}\s+(\d{{3}})", block)   # `vars @m <var> NNN`
    if not direct or not tunnel:
        return False
    return direct.group(1).startswith("5") and not tunnel.group(1).startswith("5")


def _marker_name(block):
    m = re.search(r"header\s+(X-\S+)\s+true", block)
    return m.group(1) if m else None


def _read_502():
    with open(PUBLIC_502, encoding="utf-8") as fh:
        return fh.read()


# --- the helper block itself ------------------------------------------------ #

def test_not_ready_block_matches_option_b_contract():
    block = ccm.get_not_ready_handler_block()
    assert "Cf-Ray" in block, "must gate the 200 on the Cloudflare tunnel header"
    assert "handle_errors 502 503 504" in block, "must also catch 503/504 not-ready states"
    assert "no-store" in block.lower()
    assert _placeholder_contract_ok(block)


# --- full generated Caddyfile ----------------------------------------------- #

def _render(monkeypatch):
    monkeypatch.setenv("VAST_CONTAINERLABEL", "testlabel")
    monkeypatch.setenv("VAST_TCP_PORT_6006", "1")
    monkeypatch.setenv("ENABLE_AUTH", "false")
    monkeypatch.setenv("ENABLE_HTTPS", "false")
    cfg = {
        "Tensorboard": {
            "external_port": 6006, "internal_port": 16006,
            "hostname": "localhost", "open_path": "/", "name": "Tensorboard",
        }
    }
    caddyfile, _u, _pw, _tok = ccm.generate_caddyfile(cfg)
    return caddyfile


def test_generated_caddyfile_placeholder_honours_contract(monkeypatch):
    assert _placeholder_contract_ok(_render(monkeypatch))


# --- the check bites: reject the forms that reintroduce the bug ------------- #

def test_contract_rejects_legacy_raw_502_block():
    """Pre-ADR-0017: bare 502 loader, no var, no marker → CDN-hijacked."""
    legacy = "handle_errors 502 {\n    rewrite * /502.html\n    file_server\n}"
    assert not _placeholder_contract_ok(legacy)


def test_contract_rejects_always_200():
    """Option A (always 200, no CF gate) is rejected: it drops the direct-path 5xx
    that keeps probers / Vast from reading a booting app as 'ready'."""
    always_200 = (
        "vars not_ready_status 200\n    vars @cf_tunnel not_ready_status 200\n"
        "    handle_errors 502 503 504 {\n        header X-Portal-Placeholder true\n"
        "        file_server {\n            status {vars.not_ready_status}\n        }\n    }"
    )
    assert not _placeholder_contract_ok(always_200)


def test_contract_rejects_5xx_tunnel_override():
    """A tunnel override that is still 5xx would be hijacked by Cloudflare."""
    bad = (
        "vars not_ready_status 502\n    vars @cf_tunnel not_ready_status 503\n"
        "    handle_errors 502 503 504 {\n        header X-Portal-Placeholder true\n"
        "        file_server {\n            status {vars.not_ready_status}\n        }\n    }"
    )
    assert not _placeholder_contract_ok(bad)


# --- generator <-> poll agreement (round-trip) ------------------------------ #

def test_marker_name_roundtrips_to_poll():
    """The header the generator emits must be the exact one the poll reads — a
    rename on either side breaks this (ADR 0017 finding 1)."""
    name = _marker_name(ccm.get_not_ready_handler_block())
    assert name, "generator must emit an X-* marker header"
    html = _read_502()
    assert f"headers.get('{name}')" in html or f'headers.get("{name}")' in html, \
        f"502.html poll must read the same marker name ({name}) the generator emits"


def test_poll_reload_condition_is_pinned():
    """Pin the poll's exact semantics so a boolean inversion, a removed status
    guard, or a deleted reload turns CI red (ADR 0017 finding 1)."""
    html = _read_502()
    assert re.search(r"if\s*\(\s*!\s*\w+\s*&&\s*response\.status\s*<\s*500\s*\)", html), \
        "poll must reload only when the marker is ABSENT and status < 500"
    assert "window.location.reload()" in html, "poll must actually reload"
    # Must not gate on the 502 status in any (strict-)equality form: == === != !==
    assert not re.search(r"status\s*(?:===?|!==?)\s*502", html), \
        "poll must not gate on the 502 status code (a CDN tunnel replaces it)"


# --- upstream cannot forge the marker --------------------------------------- #

def test_reverse_proxy_strips_upstream_marker():
    """A backend that emits X-Portal-Placeholder would wedge the poll forever;
    the proxy block must strip it so only Caddy can set it (ADR 0017 finding 2)."""
    block = ccm.get_reverse_proxy_block("localhost", 18080, "-1")
    assert "header_down -X-Portal-Placeholder" in block


# --- load_config: a present-but-unusable /etc/portal.yaml is ABSENT, never a crash ---
#
# caddy.sh `touch`es /etc/portal.yaml when PORTAL_CONFIG is unset, leaving a zero-byte
# file that persists in overlayfs. On a later boot — 10-prep-env.sh rewrites
# PORTAL_CONFIG from VAST_TCP_PORT_8080, which changes across stop/start — load_config
# saw the file exists and did yaml.safe_load('')['applications'] → TypeError. main()
# swallows it, so the visible symptom is no Caddyfile: no proxy, and every WebUI gated
# on portal.yaml skips startup. The invariant: fall through to PORTAL_CONFIG instead.

import pytest  # noqa: E402
import yaml as _yaml  # noqa: E402

_CFG = "localhost:1111:11111:/:Instance Portal|localhost:7860:17860:/:App UI"


@pytest.mark.parametrize("content,label", [
    ("", "zero-byte file left by caddy.sh"),
    ("something_else: true\n", "valid YAML, no applications key"),
    ("just a bare string\n", "YAML scalar, not a mapping"),
    ("applications: [unclosed\n", "truncated/corrupt doc (non-atomic write + kill)"),
    ("applications:\n  - a\n  - b\n", "applications is a list, not a map"),
])
def test_unusable_cache_falls_through_to_env(tmp_path, monkeypatch, content, label):
    p = tmp_path / "portal.yaml"
    p.write_text(content)
    monkeypatch.setenv("PORTAL_CONFIG", _CFG)
    apps = ccm.load_config(str(p))
    assert set(apps) == {"Instance Portal", "App UI"}, label
    assert apps["App UI"]["external_port"] == 7860


def test_unreadable_cache_falls_through(tmp_path, monkeypatch):
    """An unreadable file is an OSError, not a YAML error — same fall-through."""
    p = tmp_path / "portal.yaml"
    p.write_text("applications: {}\n")
    p.chmod(0o000)
    if os.access(str(p), os.R_OK):  # running as root: chmod does not deny us
        pytest.skip("running as root; cannot make a file unreadable")
    monkeypatch.setenv("PORTAL_CONFIG", _CFG)
    assert set(ccm.load_config(str(p))) == {"Instance Portal", "App UI"}


def test_no_cache_and_no_env_raises_valueerror(tmp_path, monkeypatch):
    """No config anywhere stays the graceful ValueError, not a TypeError crash."""
    p = tmp_path / "portal.yaml"
    p.write_text("")
    monkeypatch.delenv("PORTAL_CONFIG", raising=False)
    with pytest.raises(ValueError):
        ccm.load_config(str(p))


def test_valid_cache_wins_over_env(tmp_path, monkeypatch):
    """A populated cache is authoritative; the env is ignored (cache-preferred)."""
    p = tmp_path / "portal.yaml"
    p.write_text(
        "applications:\n"
        "  Cached App:\n"
        "    hostname: localhost\n"
        "    external_port: 9000\n"
        "    internal_port: 19000\n"
        "    open_path: /\n"
        "    name: Cached App\n"
    )
    monkeypatch.setenv("PORTAL_CONFIG", _CFG)  # must be ignored
    assert set(ccm.load_config(str(p))) == {"Cached App"}


def test_empty_applications_map_regenerates(tmp_path, monkeypatch):
    """An empty applications map must regenerate, not be honoured as "no apps".

    Verified on a live instance: with `applications: {}` Caddy emits a Caddyfile with
    no front ports — :1111 disappears and every external route into the box goes with
    it, and nothing restores it. Whatever an operator meant by emptying the map, the
    self-healing fall-through is the safer reading of it.
    """
    p = tmp_path / "portal.yaml"
    p.write_text("applications: {}\n")
    monkeypatch.setenv("PORTAL_CONFIG", _CFG)
    assert set(ccm.load_config(str(p))) == {"Instance Portal", "App UI"}


def test_regenerated_cache_is_well_formed(tmp_path, monkeypatch):
    """The file written on fall-through must read back without falling through again."""
    p = tmp_path / "portal.yaml"
    p.write_text("")
    monkeypatch.setenv("PORTAL_CONFIG", _CFG)
    ccm.load_config(str(p))
    doc = _yaml.safe_load(p.read_text())
    assert isinstance(doc, dict)
    assert set(doc["applications"]) == {"Instance Portal", "App UI"}
