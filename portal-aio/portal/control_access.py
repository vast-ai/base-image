"""Locality enforcement for the portal's exec-capable control routes.

The portal serves two exec-capable routes — ``POST /capabilities/provision`` and
``POST /supervisor/process/{name}/{start,stop,restart}`` — that are meant to be
reached only by a process *inside the container* talking to ``127.0.0.1:11111``
directly. Every remote path to them arrives through a proxy that stamps standard
forwarding headers:

* the Caddy edge sets ``X-Forwarded-Proto`` and ``X-Real-IP`` on every request it
  proxies to :11111 (see ``caddy_manager/caddy_config_manager.get_reverse_proxy_block``),
  and Caddy additionally appends ``X-Forwarded-For``/``X-Forwarded-Host``;
* a cloudflared quick tunnel sets the same ``X-Forwarded-*`` family.

A genuine in-container caller (``curl http://localhost:11111/...`` from a shell,
the in-image agents, a local script) sets none of them. Refusing any request that
carries a forwarding header therefore enforces localhost-only *at the asset
itself*, independent of how the request was routed. This is the authoritative
control: it closes the public Caddy edge, the self-tunnel bypass for **any**
hostname spelling that resolves to loopback (DNS-to-loopback, IPv4-mapped IPv6,
trailing-dot FQDN — none of which a spelling-based target check can enumerate),
and any Caddy path-normalization evasion, in one place. The Caddy edge deny and
the tunnel-target guard remain as cheap early refusals in front of it, but this
check is what makes the control complete.
"""
from fastapi import HTTPException, Request

# Presence of any of these means the request passed through a proxy (the Caddy
# edge or a cloudflared tunnel) rather than originating from a local in-container
# caller. The list is a superset of what Caddy and cloudflared actually stamp, so
# a future proxy that sets only a subset is still caught.
PROXY_HEADERS = (
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "forwarded",
    # Cloudflare's edge stamps these on every request through a (quick) tunnel and
    # a local caller never sets them — belt for the self-tunnel-to-loopback path.
    "cf-connecting-ip",
    "cf-ray",
)


def is_proxied(headers) -> bool:
    """True if ``headers`` carries any proxy/forwarding marker.

    ``headers`` is anything supporting case-insensitive ``in`` (a Starlette
    ``Headers`` or a plain lower-cased dict).
    """
    return any(h in headers for h in PROXY_HEADERS)


async def require_local_caller(request: Request) -> None:
    """FastAPI dependency: 403 unless the request originated on localhost.

    Attach to the exec-capable control routes. A direct ``127.0.0.1:11111`` caller
    passes; anything that reached the route through the Caddy edge or a tunnel is
    refused.
    """
    if is_proxied(request.headers):
        raise HTTPException(
            status_code=403,
            detail="This control route is available on localhost only",
        )
