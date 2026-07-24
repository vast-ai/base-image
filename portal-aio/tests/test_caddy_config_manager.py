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
    html = open(PUBLIC_502, encoding="utf-8").read()
    assert f"headers.get('{name}')" in html or f'headers.get("{name}")' in html, \
        f"502.html poll must read the same marker name ({name}) the generator emits"


def test_poll_reload_condition_is_pinned():
    """Pin the poll's exact semantics so a boolean inversion, a removed status
    guard, or a deleted reload turns CI red (ADR 0017 finding 1)."""
    html = open(PUBLIC_502, encoding="utf-8").read()
    assert re.search(r"if\s*\(\s*!\s*\w+\s*&&\s*response\.status\s*<\s*500\s*\)", html), \
        "poll must reload only when the marker is ABSENT and status < 500"
    assert "window.location.reload()" in html, "poll must actually reload"
    stripped = html.replace(" ", "")
    assert "status!=502" not in stripped and "status==502" not in stripped, \
        "poll must not gate on the 502 status code (a CDN tunnel replaces it)"


# --- upstream cannot forge the marker --------------------------------------- #

def test_reverse_proxy_strips_upstream_marker():
    """A backend that emits X-Portal-Placeholder would wedge the poll forever;
    the proxy block must strip it so only Caddy can set it (ADR 0017 finding 2)."""
    block = ccm.get_reverse_proxy_block("localhost", 18080, "-1")
    assert "header_down -X-Portal-Placeholder" in block
