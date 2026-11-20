"""The app-side locality check that makes the exec control routes localhost-only.

This is the authoritative half of ADR 0064: the Caddy edge deny and the
tunnel-target guard are early refusals, but a request that reaches
/capabilities/provision having passed through ANY proxy
(the edge, or a cloudflared tunnel aimed at loopback by any hostname spelling)
carries a forwarding header, and this check refuses it at the asset. A direct
in-container caller carries none and passes.

These tests pin the check's behaviour at the unit level; that the dependency is
actually attached to POST /capabilities/provision (and NOT to the supervisor
routes) is gated separately by test_provision_route_wiring.py, which imports the
real app and inspects the route. A live test on a real instance confirms the
end-to-end path.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from starlette.datastructures import Headers

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portal"))
import control_access as ca  # noqa: E402


class _Req:
    """Minimal stand-in for starlette.Request exposing case-insensitive headers."""

    def __init__(self, headers: dict):
        self.headers = Headers(headers)


# Every header that marks a request as having come through the Caddy edge or a
# cloudflared tunnel. Caddy stamps X-Forwarded-Proto + X-Real-IP (and appends
# X-Forwarded-For/Host); cloudflared stamps the X-Forwarded-* family.
@pytest.mark.parametrize(
    "header",
    [
        "X-Forwarded-For",
        "X-Forwarded-Host",
        "X-Forwarded-Proto",
        "X-Real-IP",
        "Forwarded",
        "Cf-Connecting-Ip",  # set by Cloudflare's edge on tunnelled requests
        "Cf-Ray",
        "x-forwarded-for",   # case-insensitive
    ],
)
def test_proxied_request_is_refused(header):
    req = _Req({header: "anything"})
    assert ca.is_proxied(req.headers) is True
    with pytest.raises(ca.HTTPException) as exc:
        asyncio.run(ca.require_local_caller(req))
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {},                                   # a bare local curl
        {"Host": "localhost:11111"},          # direct, with a plain Host
        {"User-Agent": "python-requests"},    # a local script
        {"Content-Type": "application/json"},
    ],
)
def test_direct_local_request_passes(headers):
    req = _Req(headers)
    # No forwarding header present -> treated as a local in-container caller.
    assert ca.is_proxied(req.headers) is False
    # Must not raise.
    assert asyncio.run(ca.require_local_caller(req)) is None
