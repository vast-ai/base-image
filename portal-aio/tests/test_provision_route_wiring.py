"""The provision route is actually WIRED to the locality check (ADR 0064).

`test_control_route_local_only.py` proves the `require_local_caller` helper
refuses a proxied request. But the authoritative control is only real if that
helper is *attached* to `POST /capabilities/provision`. Delete the one line
``dependencies=[Depends(require_local_caller)]`` in portal.py and every
helper-level test stays green while the tunnel-to-loopback path ADR 0064 exists
to close silently reopens. This test imports the REAL FastAPI ``app`` object and
asserts the dependency is on the route, so that regression fails CI here.

It is deliberately hermetic: the portal module pulls in hardware/IO libraries
(GPUtil, psutil, httpx, aiofiles) that are irrelevant to routing and absent from
the unit-test environment, so they are stubbed before import. Jinja2 and the
static/template dirs are real — the app object is the genuine one the server runs.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

PORTAL_DIR = Path(__file__).resolve().parents[1] / "portal"


def _stub(name: str, **attrs) -> None:
    """Install a stand-in module so `import <name>` succeeds at portal import."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules.setdefault(name, mod)


@pytest.fixture(scope="module")
def portal_app():
    """Import portal.py's real `app`, neutralising unrelated subsystems.

    portal.py builds StaticFiles(directory="static") and Jinja2Templates(
    directory="templates") at import time with paths relative to the portal dir,
    so we import with that as cwd and restore afterwards.
    """
    # Hardware/IO libs used only inside request handlers, not in routing.
    _stub("GPUtil", getGPUs=lambda: [])
    _stub("psutil")
    _stub("aiofiles")
    # httpx is used at module scope for a Timeout constant; a permissive stub
    # is enough to import (real HTTP calls never run in this test).
    _stub("httpx", Timeout=lambda *a, **k: None, AsyncClient=object)

    sys.path.insert(0, str(PORTAL_DIR))
    prev_cwd = os.getcwd()
    os.chdir(PORTAL_DIR)
    try:
        import portal  # noqa: E402  (import after stubs + chdir, by design)
        return portal
    finally:
        os.chdir(prev_cwd)


def _route(app, path: str, method: str):
    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    return None


def test_provision_route_depends_on_require_local_caller(portal_app):
    """The authoritative locality dependency is attached to the provision route."""
    from control_access import require_local_caller

    route = _route(portal_app.app, "/capabilities/provision", "POST")
    assert route is not None, "POST /capabilities/provision route is missing"

    attached = [getattr(d, "dependency", None) for d in route.dependencies]
    assert require_local_caller in attached, (
        "POST /capabilities/provision no longer depends on require_local_caller — "
        "the localhost-only control (ADR 0064) has been unwired. Restore "
        "dependencies=[Depends(require_local_caller)] on the route."
    )


def test_supervisor_routes_are_not_gated_by_the_locality_check(portal_app):
    """Supervisor controls stay remotely reachable (ADR 0064 scope); the locality
    dependency must NOT leak onto them, or the Services UI breaks over the edge."""
    from control_access import require_local_caller

    for name in ("start", "stop", "restart"):
        route = _route(portal_app.app, f"/supervisor/process/{{name}}/{name}", "POST")
        assert route is not None, f"/supervisor/process/.../{name} route is missing"
        attached = [getattr(d, "dependency", None) for d in route.dependencies]
        assert require_local_caller not in attached, (
            f"/supervisor/process/.../{name} is wrongly gated by require_local_caller; "
            "that re-introduces the reverted over-broad scope (ADR 0064)."
        )
