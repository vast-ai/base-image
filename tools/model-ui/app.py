#!/usr/bin/env python3
"""Model UI — Lightweight web interface for vLLM, vLLM-Omni, and SGLang.

Serves a single-page HTML app and proxies API calls to the inference backend.
No heavy UI frameworks — just HTML/CSS/JS with a Starlette proxy.
"""

import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response, StreamingResponse
from starlette.routing import Route

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE = os.environ.get("VLLM_API_BASE", "http://localhost:18000")
MODEL_NAME = os.environ.get("MODEL_NAME", "")
UI_MODE_OVERRIDE = os.environ.get("UI_MODE", "").lower().strip()
_TABS_OVERRIDE = os.environ.get("MODEL_UI_TABS", "").strip()

POLL_INTERVAL = 5
POLL_TIMEOUT = 600

# ---------------------------------------------------------------------------
# Mode detection — picks default tab
# ---------------------------------------------------------------------------

_IMAGE_RE = re.compile(
    r"flux|stable[_-]?diffusion|sd3|sdxl|hunyuan.*image|qwen.*image"
    r"|playground|pixart|imagen|z[_-]?image",
    re.I,
)
_TTS_RE = re.compile(r"tts|speech|cosyvoice|parler|bark|xtts", re.I)
_OMNI_RE = re.compile(r"omni|bagel", re.I)
_VIDEO_RE = re.compile(r"wan|hunyuan.*video|cogvideo", re.I)


def detect_default_tab(model_id: str) -> str:
    if UI_MODE_OVERRIDE:
        return {
            "chat": "chat", "image": "image", "video": "video",
            "tts": "tts", "omni": "chat",
        }.get(UI_MODE_OVERRIDE, "chat")
    name = model_id or MODEL_NAME
    if _VIDEO_RE.search(name):
        return "video"
    if _IMAGE_RE.search(name):
        return "image"
    if _TTS_RE.search(name):
        return "tts"
    if _OMNI_RE.search(name):
        return "chat"
    return "chat"


# ---------------------------------------------------------------------------
# Wait for API
# ---------------------------------------------------------------------------


def wait_for_api() -> str:
    """Poll GET /v1/models until the API is up. Returns first model id."""
    deadline = time.monotonic() + POLL_TIMEOUT
    with httpx.Client() as client:
        while time.monotonic() < deadline:
            try:
                r = client.get(f"{API_BASE}/v1/models", timeout=10)
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    if data:
                        return data[0]["id"]
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
                pass
            print(f"[model-ui] Waiting for API at {API_BASE} ...")
            time.sleep(POLL_INTERVAL)
    print("[model-ui] Timed out waiting for API", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

print("[model-ui] Waiting for inference API...")
model_id = wait_for_api()
print(f"[model-ui] API ready — model: {model_id}")

default_tab = detect_default_tab(model_id)
print(f"[model-ui] Default tab: {default_tab}")

short_name = model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id

# Load HTML and inject config as JSON (escape "<" for safe embedding in <script> tags)
_html = (Path(__file__).parent / "index.html").read_text()
_allowed_tabs = [('chat' if t.strip().lower() == 'omni' else t.strip().lower()) for t in _TABS_OVERRIDE.split(",") if t.strip()] or None
_config_json = json.dumps(
    {"modelId": model_id, "modelShort": short_name, "defaultTab": default_tab, "allowedTabs": _allowed_tabs}
)
_config_json_safe = _config_json.replace("<", "\\u003c")
_html = _html.replace("__CONFIG_JSON__", _config_json_safe)

_client = None  # assigned in lifespan


@asynccontextmanager
async def lifespan(app):
    global _client
    _client = httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(300, connect=10))
    yield
    await _client.aclose()

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def index(request: Request):
    return HTMLResponse(_html)


async def proxy(request: Request):
    path = request.path_params["path"]
    url = f"/v1/{path}"

    try:
        if request.method == "GET":
            r = await _client.get(url, params=dict(request.query_params))
            return Response(
                content=r.content,
                status_code=r.status_code,
                media_type=r.headers.get("content-type", "application/json"),
            )

        body = await request.body()
        content_type = request.headers.get("content-type", "application/json")

        # Detect streaming requests
        is_stream = False
        if "json" in content_type:
            try:
                is_stream = json.loads(body).get("stream", False)
            except (json.JSONDecodeError, AttributeError):
                pass

        if is_stream:
            req = _client.build_request(
                "POST", url, content=body,
                headers={"content-type": "application/json"},
                params=dict(request.query_params),
            )
            r = await _client.send(req, stream=True)

            async def generate():
                async for chunk in r.aiter_bytes():
                    yield chunk
                await r.aclose()

            return StreamingResponse(
                generate(),
                status_code=r.status_code,
                media_type="text/event-stream",
            )

        # Non-streaming POST
        r = await _client.post(
            url, content=body, headers={"content-type": content_type},
            params=dict(request.query_params),
        )
        return Response(
            content=r.content,
            status_code=r.status_code,
            media_type=r.headers.get("content-type", "application/json"),
        )

    except httpx.ConnectError:
        return Response(
            content=json.dumps({"error": "Inference API unavailable"}),
            status_code=502,
            media_type="application/json",
        )
    except httpx.ReadTimeout:
        return Response(
            content=json.dumps({"error": "Request timed out"}),
            status_code=504,
            media_type="application/json",
        )


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/", index),
        Route("/api/{path:path}", proxy, methods=["GET", "POST"]),
    ],
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=17860, log_level="info")
