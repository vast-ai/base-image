"""Runtime bind-address smoke gate (ADR 0002 binding condition 1).

This is the REAL safety gate that the static L051 check defers to. The static
linter can prove "EXPOSE only ports some entry proxies", but it cannot see where a
process actually binds — and app bind ports here are routinely opaque (`npm run
start`, `--port` flags, env overrides). Both prior loopback-exposure incidents were
apps binding `0.0.0.0` instead of `127.0.0.1`. So the gate is: boot the image,
dump `ss -ltnp` + the rendered portal config, and assert nothing reachable is bound
public without Caddy in front.

Pure verdict logic lives here (unit-tested); the container orchestration is the
thin bash harness `tools/imagegen/smoke/bind-check.sh`, which collects the artifacts
and calls `imagegen bindcheck`.

Threat model / rules:
- A "public" listener binds 0.0.0.0 / :: / * (reachable once the host maps the port);
  a loopback listener binds 127.0.0.0/8 or ::1.
- Caddy is the auth front: a Caddy public listener on a *proxied* external port is
  expected and fine. Any OTHER process public on a mapped port, or anything public
  on a port Caddy does not front, is a raw unauthenticated exposure.
- Concretely, FAIL on:
  1. a public listener on any `forbidden_expose_ports` (internal app ports +
     equal-port-only ports) — the app-binds-0.0.0.0 root cause;
  2. a non-Caddy public listener on an EXPOSE'd port — something other than the
     auth proxy is publicly bound where the host maps traffic;
  3. an EXPOSE'd port that is equal-port-only (Caddy skips it) with any public
     listener — externally mapped, no auth front.
  WARN on a proxied EXPOSE'd port with no Caddy listener (functional: the proxy did
  not come up; not a security failure).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .dockerfile import parse
from .portal import (
    PortalEntry,
    equal_port_externals,
    forbidden_expose_ports,
    proxied_externals,
)

# Processes legitimately allowed to bind a public address on a mapped port.
# Caddy IS the TLS+token auth front; everything else must bind loopback.
DEFAULT_PROXY_PROCESSES: frozenset[str] = frozenset({"caddy"})

ERROR = "ERROR"
WARN = "WARN"

_LOOPBACK_RE = re.compile(r"^(127\.|::1$|\[::1\]$)")
_PUBLIC_ADDRS = {"0.0.0.0", "*", "::", "[::]", "0", "[::]%0"}


@dataclass(frozen=True)
class Listener:
    addr: str
    port: int
    process: str | None  # process name from ss, or None if not captured

    @property
    def public(self) -> bool:
        a = self.addr
        if _LOOPBACK_RE.match(a):
            return False
        return a in _PUBLIC_ADDRS or a == "::" or a.startswith("[::]") or a == "*"


@dataclass(frozen=True)
class Violation:
    severity: str
    port: int
    detail: str


def parse_ss(output: str) -> list[Listener]:
    """Parse `ss -ltnp` output. Tolerant of the header line, IPv6 bracket forms, and
    a missing process column. The Local Address:Port column has no internal spaces,
    so whitespace splitting is safe; the address is everything before the last ':'."""
    listeners: list[Listener] = []
    for line in output.splitlines():
        cols = line.split()
        if len(cols) < 4 or cols[0].upper() != "LISTEN":
            continue  # skip header and any non-LISTEN row
        local = cols[3]
        if ":" not in local:
            continue
        addr, _, port_s = local.rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            continue
        proc = None
        m = re.search(r'users:\(\("([^"]+)"', line)
        if m:
            proc = m.group(1)
        listeners.append(Listener(addr=addr, port=port, process=proc))
    return listeners


def exposed_ports(dockerfile_text: str) -> set[int]:
    """The set of EXPOSE'd ports declared in a Dockerfile (the externally-mapped set
    in a no-template run). Strips `/tcp`/`/udp`; ignores unresolved `${VARS}`."""
    ports: set[int] = set()
    for ins in parse(dockerfile_text):
        if ins.cmd != "EXPOSE":
            continue
        for tok in ins.value.split():
            tok = tok.split("/", 1)[0]
            if tok.isdigit():
                ports.add(int(tok))
    return ports


def check_binds(
    listeners: Iterable[Listener],
    exposed: set[int],
    entries: Iterable[PortalEntry],
    *,
    proxy_processes: frozenset[str] = DEFAULT_PROXY_PROCESSES,
) -> list[Violation]:
    """Return the bind-address violations (ADR 0002 condition 1). Empty list = pass."""
    listeners = list(listeners)
    entries = list(entries)
    public = [l for l in listeners if l.public]
    proxied = proxied_externals(entries)
    equal_only = equal_port_externals(entries) - proxied
    forbidden = forbidden_expose_ports(entries)

    out: list[Violation] = []

    # (1) anything public on a loopback-only app port (internals + equal-only) —
    #     the app-binds-0.0.0.0 root cause, regardless of EXPOSE.
    for l in public:
        if l.port in forbidden:
            out.append(Violation(
                ERROR, l.port,
                f"{l.process or '?'} binds {l.addr}:{l.port} — must be loopback "
                f"(no Caddy auth front; this port is an app/internal or equal-port-only port)"))

    # (2) non-Caddy public listener on an EXPOSE'd (mapped) port.
    for l in public:
        if l.port in exposed and (l.process or "") not in proxy_processes:
            out.append(Violation(
                ERROR, l.port,
                f"{l.process or '?'} (not an auth proxy) binds {l.addr}:{l.port}, "
                f"an EXPOSE'd port — externally mapped without Caddy auth"))

    # (3) EXPOSE'd port that Caddy does not front (equal-port-only) with any public bind.
    for port in sorted(exposed & equal_only):
        if any(l.public and l.port == port for l in listeners):
            out.append(Violation(
                ERROR, port,
                f"port {port} is EXPOSE'd but equal-port-only (Caddy skips it) and has a "
                f"public listener — raw, unauthenticated, externally mapped"))

    # (WARN) proxied EXPOSE'd port with no Caddy listener at all (functional).
    caddy_ports = {l.port for l in listeners if (l.process or "") in proxy_processes}
    for port in sorted(exposed & proxied):
        if port not in caddy_ports:
            out.append(Violation(
                WARN, port,
                f"EXPOSE'd proxied port {port} has no Caddy listener — the proxy did not "
                f"come up (functional, not a security failure)"))

    # de-dup identical (severity, port, detail)
    seen: set[tuple[str, int, str]] = set()
    deduped: list[Violation] = []
    for v in out:
        key = (v.severity, v.port, v.detail)
        if key not in seen:
            seen.add(key)
            deduped.append(v)
    return deduped
