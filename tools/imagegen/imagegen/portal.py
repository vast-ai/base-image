"""Canonical PORTAL_CONFIG model — the single in-repo definition of the field
order and the EXPOSE port-set logic (ADR 0002).

This MIRRORS the runtime parser (portal-aio/caddy_manager/caddy_config_manager.py):

    hostname, ext_port, int_port, path, name = app_string.split(':', 4)   # line ~26

and its proxy gate:

    if external_port == internal_port or not VAST_TCP_PORT_<external>: continue   # line ~217

i.e. Caddy stands up an auth site on `:external` ONLY when `external != internal`
AND the host mapped that port. The host-mapping half is runtime-only, so here we
*over-approximate* "proxied" as `external != internal` — which fails safe: it can
only treat a port as more-proxied (more EXPOSE-eligible), never less.

If caddy_config_manager.py changes its format or skip rule, update this module and
the pin test (tests/test_portal.py::test_field_order_pinned_to_runtime_parser).

Field order (authoritative): localhost:<external>:<internal>:<path>:<Label>
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Canonical PORTAL_CONFIG field order (see caddy_config_manager.py split above).
FIELD_ORDER: tuple[str, ...] = ("hostname", "external", "internal", "path", "label")

# Ports the base seeds and the platform auto-opens (Instance Portal 1111, Jupyter
# 8080); excluded from per-image EXPOSE requirements.
# NOTE — ADR 0002 binding condition 4: this encodes a platform assumption (that
# 1111/8080 are mapped for us). Keep in sync with the base 10-prep-env.sh / justify
# by test before relying on it as a security gate.
BASE_ALLOWLIST: frozenset[int] = frozenset({1111, 8080})


@dataclass(frozen=True)
class PortalEntry:
    hostname: str
    external: int
    internal: int
    path: str
    label: str

    @property
    def proxied(self) -> bool:
        """True iff Caddy fronts this entry with an auth site (external != internal).

        Mirrors the `external_port == internal_port` half of caddy_config_manager.py's
        skip gate; the `VAST_TCP_PORT_<external>` (host-mapped) half is runtime-only
        and intentionally not modelled (over-approximating proxied fails safe)."""
        return self.external != self.internal


def parse_portal_config(text: str) -> list[PortalEntry]:
    """Parse a PORTAL_CONFIG string, mirroring the runtime parser: split on '|',
    then `split(':', 4)`. Blank segments are skipped (a trailing '|' is tolerated).

    Raises ValueError on a segment that does not have exactly 5 colon-fields or
    whose port fields are not integers — the same shapes that would crash the
    runtime parser, surfaced early instead of at boot."""
    entries: list[PortalEntry] = []
    for seg in text.split("|"):
        seg = seg.strip()
        if not seg:
            continue
        parts = seg.split(":", 4)
        if len(parts) != 5:
            raise ValueError(f"PORTAL_CONFIG entry needs 5 colon-fields, got {len(parts)}: {seg!r}")
        hostname, ext, intl, path, label = parts
        try:
            ext_i, int_i = int(ext), int(intl)
        except ValueError:
            raise ValueError(f"PORTAL_CONFIG entry has non-integer port(s): {seg!r}")
        entries.append(PortalEntry(hostname, ext_i, int_i, path, label))
    return entries


# ---- EXPOSE port-set logic (ADR 0002) ---------------------------------------
#
# A port is EXPOSE-safe iff *some* entry proxies it. The forced-proxy entry
# (external != internal) licenses the EXPOSE; equal-port siblings on the same
# external port (the multi-URL tab convention) are inert to it. `required` and
# `forbidden` are disjoint by construction (proxied is subtracted out of
# forbidden), so a port is never simultaneously required and forbidden.

def proxied_externals(entries: Iterable[PortalEntry]) -> set[int]:
    """External ports Caddy fronts with an auth site (external != internal)."""
    return {e.external for e in entries if e.proxied}


def internal_ports(entries: Iterable[PortalEntry]) -> set[int]:
    """All internal (app loopback) ports across entries."""
    return {e.internal for e in entries}


def equal_port_externals(entries: Iterable[PortalEntry]) -> set[int]:
    """Ports of equal-port entries (external == internal). Caddy skips these — no
    site, no auth — so they are tab metadata only, never a proxied front."""
    return {e.external for e in entries if not e.proxied}


def required_expose_ports(
    entries: Iterable[PortalEntry], allowlist: frozenset[int] = BASE_ALLOWLIST
) -> set[int]:
    """Caddy-front external ports an image should EXPOSE: proxied externals minus
    the base-owned/auto-opened allowlist."""
    entries = list(entries)
    return proxied_externals(entries) - allowlist


def forbidden_expose_ports(entries: Iterable[PortalEntry]) -> set[int]:
    """Ports that must NEVER be EXPOSEd: any internal or equal-port value that NO
    entry proxies (EXPOSE-ing them would auto-request a Vast mapping for a port
    Caddy never fronts → a raw, unauthenticated backend). Disjoint from
    required_expose_ports because proxied externals are subtracted out."""
    entries = list(entries)
    proxied = proxied_externals(entries)
    return (internal_ports(entries) | equal_port_externals(entries)) - proxied


def orphan_equal_ports(entries: Iterable[PortalEntry]) -> set[int]:
    """Equal-port entries with no proxied sibling on the same external port — a
    'dead tab or raw unauthenticated port' smell (e.g. a misdeclared entry whose
    app actually binds elsewhere). Advisory: the bind smoke-check is the real gate
    (ADR 0002 binding condition 1)."""
    entries = list(entries)
    return equal_port_externals(entries) - proxied_externals(entries)


# ---- baked-default extraction from a boot env script ------------------------

_GUARD_RE = re.compile(r"if\s*\[\[?\s*-z\s+[^\n]*PORTAL_CONFIG")
_ASSIGN_RE = re.compile(r"""(?:export\s+)?PORTAL_CONFIG=(["'])(.*?)\1""", re.DOTALL)


def extract_baked_default(env_sh_text: str) -> str | None:
    """Return the literal PORTAL_CONFIG baked behind the `if [[ -z ... ]]` guard in
    a `05-<name>-env.sh` boot script, or None if the script bakes no default.

    CAVEAT — ADR 0002 binding condition 2: this returns the baked DEFAULT literal.
    It does NOT apply later runtime mutations *in the same script* (e.g. the
    linux-desktop selkies `grep -v ':16100:'` strip, which runs after this guard)
    nor the base 10-prep-env.sh Jupyter rewrite. The authoritative runtime value
    must come from a dumped rendered config in the smoke test, not this function.
    Matching is scoped to the assignment that follows the unset-guard, so a later
    `PORTAL_CONFIG=$(echo ... )` mutation is deliberately ignored."""
    guard = _GUARD_RE.search(env_sh_text)
    if not guard:
        return None
    m = _ASSIGN_RE.search(env_sh_text, guard.end())
    return m.group(2) if m else None
