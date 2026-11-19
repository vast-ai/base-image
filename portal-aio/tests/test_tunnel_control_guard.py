"""The quick-tunnel guard that keeps the localhost-only control routes closed.

The portal's /capabilities/provision route is denied
to non-local callers at the Caddy edge (test_caddy_config_manager.py proves the
deny compiles ahead of the proxy). That deny sits on the public :1111 edge only.
The portal's own quick-tunnel feature runs a user-driven cloudflared process that
exposes a local target to the public internet, so a token holder who points it at
the portal's internal control port (127.0.0.1:11111) would re-expose exactly the
routes the deny closes, around the deny. tunnel_manager must refuse that target.

These tests pin the guard's boundary: it refuses the portal's loopback control
port on every spelling of "local", and leaves every other target — including the
free-form quick tunnels the feature exists for — alone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tunnel_manager"))
import tunnel_manager as tm  # noqa: E402


@pytest.mark.parametrize(
    "target",
    [
        "localhost:11111",
        "http://localhost:11111",
        "http://localhost:11111/capabilities/provision",
        "https://localhost:11111",
        "127.0.0.1:11111",
        "http://127.0.0.1:11111",
        "127.0.0.2:11111",          # anywhere in 127.0.0.0/8 is loopback
        "http://[::1]:11111",       # IPv6 loopback
        "0.0.0.0:11111",            # unspecified; reaches localhost on Linux
        "http://0.0.0.0:11111",
    ],
)
def test_guard_refuses_portal_control_port_on_local_interfaces(target):
    assert tm._targets_portal_control_api(target) is True


@pytest.mark.parametrize(
    "target",
    [
        "localhost:8080",           # a real app behind Caddy auth
        "localhost:7860",
        "127.0.0.1:1111",           # the portal EDGE (deny already lives there)
        "http://localhost:9000",    # a user's own free-form service
        "example.com:11111",        # some other machine's port, not our portal
        "http://1.2.3.4:11111",     # a public host that happens to use 11111
        "localhost",                # no port -> not the control port
        "http://localhost/",
    ],
)
def test_guard_allows_everything_that_is_not_the_local_control_port(target):
    assert tm._targets_portal_control_api(target) is False


def test_get_or_create_rejects_control_target_with_403_before_spawning(monkeypatch):
    """The refusal must happen before any cloudflared process is started."""
    import asyncio

    spawned = []

    async def _boom(target_url):  # must never be reached for a denied target
        spawned.append(target_url)
        raise AssertionError("create_quick_tunnel called for a denied target")

    monkeypatch.setattr(tm, "create_quick_tunnel", _boom)

    with pytest.raises(tm.HTTPException) as exc:
        asyncio.run(tm.get_or_create_quick_tunnel("http://127.0.0.1:11111"))

    assert exc.value.status_code == 403
    assert spawned == []


def test_get_or_create_still_builds_an_ordinary_target(monkeypatch):
    """A normal target is not touched by the guard."""
    import asyncio

    class _FakeTunnel:
        tunnel_url = "https://example.trycloudflare.com"

    async def _ok(target_url):
        return _FakeTunnel()

    monkeypatch.setattr(tm, "create_quick_tunnel", _ok)
    # isolate from any residual module state
    monkeypatch.setattr(tm, "quick_tunnels", {})

    tunnel = asyncio.run(tm.get_or_create_quick_tunnel("http://localhost:8080"))
    assert tunnel.tunnel_url == "https://example.trycloudflare.com"
