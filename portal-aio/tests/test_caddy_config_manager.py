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
import pathlib
import re
import sys

import pytest

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


# --- CADDY_HOST_PASSTHROUGH: the app's own Origin check must keep working (ADR 0050) ---

def test_host_passthrough_forwards_browser_host_only_for_listed_ports(monkeypatch):
    """Selkies compares Origin with Host; Caddy's Host rewrite made it refuse every
    browser WebSocket. A listed port forwards Host unchanged; every other port keeps
    the rewrite."""
    monkeypatch.setenv("CADDY_HOST_PASSTHROUGH", "16100")
    assert "header_up Host" not in ccm.get_reverse_proxy_block("localhost", 16100, "-1")
    assert "header_up Host {upstream_hostport}" in ccm.get_reverse_proxy_block("localhost", 18080, "-1")


def test_host_passthrough_beats_localhost_rewrite_and_has_no_true_form(monkeypatch):
    """The localhost rewrite also rewrites Origin, which would let any site's
    WebSocket through the app's same-origin check; passthrough must win. `true`
    must not turn passthrough on for every app."""
    monkeypatch.setenv("CADDY_HEADER_UP_LOCALHOST", "true")
    monkeypatch.setenv("CADDY_HOST_PASSTHROUGH", "16100")
    block = ccm.get_reverse_proxy_block("localhost", 16100, "-1")
    assert "header_up Host" not in block and "header_up Origin" not in block
    monkeypatch.setenv("CADDY_HOST_PASSTHROUGH", "true")
    assert "header_up Origin" in ccm.get_reverse_proxy_block("localhost", 16100, "-1")


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


# ---- the admin API is off by default (measured incident, 2026-09-10) ----


def _render_with(monkeypatch, **env):
    """A minimal Caddyfile, with the caller's environment applied."""
    monkeypatch.setenv("ENABLE_AUTH", "false")
    monkeypatch.setenv("ENABLE_HTTPS", "false")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    cfg = {"Tensorboard": {"external_port": 6006, "internal_port": 16006,
                           "hostname": "localhost", "open_path": "/", "name": "Tensorboard"}}
    caddyfile, _u, _pw, _tok = ccm.generate_caddyfile(cfg)
    return caddyfile


def test_admin_api_is_off_by_default(monkeypatch):
    """THE incident. Caddy's admin API listens on 127.0.0.1:2019 by default,
    unauthenticated, and accepts POST /stop. Nothing in portal-aio uses it — there is no
    `caddy reload` anywhere, config changes land by restarting the supervisor unit — so it
    is pure surface.

    Measured on a live instance: Pinokio, which bundles its own caddy, polled GET /config/
    for three minutes and then POSTed /stop. Caddy exited 0 and supervisor did NOT restart
    it, because `autorestart=unexpected` + `exitcodes=0` treats a clean exit as expected.
    That policy is correct — it is what lets exit_serverless.sh stop units in serverless
    mode — which is precisely why the admin socket must not be able to trigger it. Any
    process in the container could take the portal's front door down, permanently."""
    assert re.search(r"^\s*admin off\s*$", _render_with(monkeypatch), re.M), \
        "the global block must disable caddy's admin API"


def test_admin_api_can_be_restored_without_a_rebuild(monkeypatch):
    """Backing this out must not require a new image: CADDY_ENABLE_ADMIN=true returns
    caddy to its default, the same escape-hatch shape as CADDY_ENABLE_COMPRESSION."""
    out = _render_with(monkeypatch, CADDY_ENABLE_ADMIN="true")
    assert not re.search(r"^\s*admin off\s*$", out, re.M)


def test_admin_off_shares_the_global_block_with_servers(monkeypatch):
    """Both directives live in the same global block; adding one must not displace the
    other. Asserted WITHOUT enabling HTTPS: that path waits on a real TLS certificate and
    gives up after five attempts, so a test that switched it on would be slow, flaky and
    testing the cert wait rather than this. The template placeholder is checked at source
    level instead, which is the thing that would actually be lost."""
    out = _render_with(monkeypatch)
    assert re.search(r"^\s*admin off\s*$", out, re.M)
    src = pathlib.Path(ccm.__file__).read_text()
    assert "{admin_block}" in src and "{servers_block}" in src, \
        "the global block must still emit both placeholders"


# --- control routes are localhost-only --------------------------- #

def _render_with_portal(monkeypatch):
    """Render a config that includes the portal (internal 11111) plus one other app."""
    monkeypatch.setenv("VAST_CONTAINERLABEL", "testlabel")
    monkeypatch.setenv("VAST_TCP_PORT_1111", "1")
    monkeypatch.setenv("VAST_TCP_PORT_6006", "1")
    monkeypatch.setenv("ENABLE_AUTH", "false")
    monkeypatch.setenv("ENABLE_HTTPS", "false")
    cfg = {
        "Instance Portal": {
            "external_port": 1111, "internal_port": 11111,
            "hostname": "localhost", "open_path": "/", "name": "Instance Portal",
        },
        "Tensorboard": {
            "external_port": 6006, "internal_port": 16006,
            "hostname": "localhost", "open_path": "/", "name": "Tensorboard",
        },
    }
    caddyfile, _u, _pw, _tok = ccm.generate_caddyfile(cfg)
    return caddyfile


def test_control_deny_block_covers_provision_only():
    b = ccm.get_control_route_deny_block()
    assert "/capabilities/provision" in b
    # The supervisor process controls are NOT denied at the edge: the portal is
    # reached remotely, so the Services UI must drive them (ADR 0064).
    assert "/supervisor" not in b
    assert "handle @vast_control_routes" in b      # a handle, not a route, so it wins in noauth too
    assert "403" in b


def test_control_deny_emitted_once_scoped_to_portal(monkeypatch):
    cf = _render_with_portal(monkeypatch)
    assert cf.count('respond "This control route is available on localhost only" 403') == 1
    assert "/capabilities/provision" in cf         # provisioner denied at the edge
    assert "/supervisor" not in cf                 # supervisor routes stay reachable remotely


def _compiled_portal_routes(cf, caddy):
    """Adapt the Caddyfile through real caddy and return the :1111 server's route list."""
    import json, os, subprocess, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".caddy", delete=False) as f:
        f.write(cf)
        path = f.name
    try:
        out = subprocess.run([caddy, "adapt", "--adapter", "caddyfile", "--config", path],
                             capture_output=True, text=True, check=True).stdout
    finally:
        os.unlink(path)
    servers = json.loads(out)["apps"]["http"]["servers"]
    srv = next(s for s in servers.values()
               if any(str(l).endswith(":1111") for l in s.get("listen", [])))
    return srv["routes"]


@pytest.mark.parametrize("enable_auth", ["true", "false"])
def test_control_deny_precedes_proxy_in_compiled_caddy(monkeypatch, enable_auth):
    """The deny must actually fire before the proxy in BOTH auth modes.

    Caddy's Caddyfile adapter sorts `handle` BEFORE `route`, so a route-based
    deny loses to the noauth catch-all `handle` and never runs. This adapts the
    generated config through real caddy and asserts the 403 deny is ordered ahead
    of the reverse_proxy — the string-order checks the earlier tests used could
    not catch that (they passed while the deny was dead code in noauth mode).
    """
    import json, os, shutil
    caddy = shutil.which("caddy")
    if not caddy and os.path.exists(ccm.CADDY_BIN):
        caddy = ccm.CADDY_BIN
    if not caddy:
        # This is the only test that proves ADR 0064 binding condition 1 (the
        # handle-based deny sorts ahead of the proxy in both auth modes). It must
        # never silently skip in CI, or the control's correctness goes unverified
        # while the suite stays green. CI sets CI_REQUIRE_CADDY and installs caddy
        # before this step; a missing binary there is a harness bug, so fail.
        if os.environ.get("CI_REQUIRE_CADDY"):
            pytest.fail("CI_REQUIRE_CADDY set but no caddy binary on PATH — "
                        "the compiled-order proof cannot run")
        pytest.skip("needs a real caddy binary to adapt the config")
    monkeypatch.setattr(ccm, "CADDY_BIN", caddy)  # hash-password is called in auth mode
    monkeypatch.setenv("VAST_CONTAINERLABEL", "ci")
    monkeypatch.setenv("VAST_TCP_PORT_1111", "1")
    monkeypatch.setenv("ENABLE_HTTPS", "false")
    monkeypatch.setenv("ENABLE_AUTH", enable_auth)
    cfg = {"Instance Portal": {"external_port": 1111, "internal_port": 11111,
           "hostname": "localhost", "open_path": "/", "name": "Instance Portal"}}
    cf, *_ = ccm.generate_caddyfile(cfg)
    routes = _compiled_portal_routes(cf, caddy)
    deny = proxy = None
    for i, r in enumerate(routes):
        blob = json.dumps(r, separators=(",", ":"))
        if deny is None and '"status_code":403' in blob and "/capabilities/provision" in blob:
            deny = i
        if proxy is None and "reverse_proxy" in blob:
            proxy = i
    assert deny is not None, "no 403 control-route deny in the compiled config"
    assert proxy is not None, "no reverse_proxy in the compiled config"
    assert deny < proxy, (
        f"ENABLE_AUTH={enable_auth}: the 403 deny (route[{deny}]) must precede the "
        f"reverse_proxy (route[{proxy}]); caddy runs `handle` before `route`")


def test_no_control_deny_without_portal(monkeypatch):
    # a config with no portal block (internal 11111) emits no control-route deny
    monkeypatch.setenv("VAST_CONTAINERLABEL", "testlabel")
    monkeypatch.setenv("VAST_TCP_PORT_6006", "1")
    monkeypatch.setenv("ENABLE_AUTH", "false")
    monkeypatch.setenv("ENABLE_HTTPS", "false")
    cfg = {"Tensorboard": {"external_port": 6006, "internal_port": 16006,
           "hostname": "localhost", "open_path": "/", "name": "Tensorboard"}}
    cf, *_ = ccm.generate_caddyfile(cfg)
    assert "vast_control_routes" not in cf
    assert "/capabilities/provision" not in cf
