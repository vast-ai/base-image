#!/usr/bin/env python3
"""Vast single-port launcher for StableDAW (theDAW).

Upstream's ``backend/server.py`` serves only the JSON API under ``/api/*``; the
React/Vite SPA is a separate dev server upstream (``vite`` on :5173, proxying
``/api`` to the backend on :8600). For the Vast deployment we serve the
pre-built SPA (``frontend/dist``, produced by ``npm run build`` at image-build
time) at ``/`` from the SAME FastAPI app, so the Instance Portal's Caddy proxy
fronts a single loopback port. The SPA calls ``/api/*`` relative, so same-origin
serving needs no API-base patch.

Binds 127.0.0.1 only (loopback-behind-Caddy invariant). Host/port/dir are
overridable via STABLEDAW_HOST / STABLEDAW_PORT / STABLEDAW_DIR.
"""
import os
import pathlib
import sys

APP_DIR = pathlib.Path(os.environ.get("STABLEDAW_DIR", "/opt/stabledaw"))
sys.path.insert(0, str(APP_DIR))

import uvicorn
from backend.server import app
from fastapi.staticfiles import StaticFiles

_dist = APP_DIR / "frontend" / "dist"
if not (_dist / "index.html").is_file():
    # Fail loudly rather than silently serving an API with no UI. The image
    # build runs `npm run build` and asserts dist/index.html exists, so a miss
    # here means the bundle was removed or STABLEDAW_DIR points somewhere wrong.
    raise RuntimeError(
        f"StableDAW frontend bundle missing at {_dist} (expected index.html)"
    )

# Mounted AFTER import, so the app's /api/* routes keep precedence; every other
# path falls through to the SPA. html=True serves index.html for client routes.
app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("STABLEDAW_HOST", "127.0.0.1"),
        port=int(os.environ.get("STABLEDAW_PORT", "18600")),
        log_level="info",
    )
