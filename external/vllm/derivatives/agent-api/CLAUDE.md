# Agent API Image

A headless, multi-modal inference image designed for consumption by AI agent frameworks
(OpenClaw, Hermes, or any OpenAI-compatible client). Exposes a unified `/v1` API on a
single port covering LLM, image gen, video gen, music gen, TTS, and STT.

## Architecture

```
                        Single exposed port
                              |
                          [Caddy gateway]
                              |
         +--------------------+--------------------+
         |                    |                    |
    [vLLM]              [Translation       [Whisper]
    /v1/chat/            Layer (FastAPI)]   /v1/audio/transcriptions
    /v1/models           /v1/images/*
    /v1/completions      /v1/audio/speech
                         /v1/videos
                         /v1/music (custom)
                              |
                         [ComfyUI API mode]
                         Template workflows
```

### Backends

| Service | Port | Role | API compatibility |
|---------|------|------|-------------------|
| vLLM | 8000 | LLM inference (Llama, Hermes, Qwen, etc.) | Native OpenAI `/v1` |
| ComfyUI | 18188 | Image, video, music (ACE-Step), TTS | Internal only — behind translation layer |
| Whisper | 7862 | Speech-to-text | Native OpenAI `/v1/audio/transcriptions` |
| Translation layer | 8100 | Converts OpenAI requests → ComfyUI workflows | Implements OpenAI endpoints |
| Caddy gateway | 3000 | Unified single-port entry | Routes to all services |

### Translation Layer

A lightweight FastAPI service (~300 lines) that:
1. Accepts standard OpenAI-format requests
2. Maps parameters to ComfyUI template workflow JSON (placeholder substitution)
3. Queues workflow via ComfyUI `/prompt` API
4. Polls `/history/{prompt_id}` for completion
5. Returns result in OpenAI response format (base64 or URL)

**Endpoints to implement:**
- `POST /v1/images/generations` → ComfyUI image workflow (Flux, SDXL, etc.)
- `POST /v1/images/edits` → ComfyUI img2img workflow
- `POST /v1/audio/speech` → ComfyUI TTS workflow (via TTS-Audio-Suite node)
- `POST /v1/videos` → ComfyUI video workflow (HunyuanVideo, WAN, etc.)
- `POST /v1/music/generations` → ComfyUI ACE-Step workflow (custom, non-standard)
- `GET /v1/models` → Aggregated model list from vLLM + available ComfyUI workflows + Whisper

**Template workflows** are stored as JSON files in `/opt/agent-api/workflows/`. Each is a
ComfyUI API-format workflow exported from the UI. The translation layer substitutes
parameters (prompt, dimensions, model, etc.) into the template before queuing.

### Caddy Gateway (Caddyfile)

```
:3000 {
    # vLLM — native OpenAI, proxy directly
    handle /v1/chat/* {
        reverse_proxy localhost:8000
    }
    handle /v1/completions* {
        reverse_proxy localhost:8000
    }

    # Whisper — native OpenAI, proxy directly
    handle /v1/audio/transcriptions* {
        reverse_proxy localhost:7862
    }

    # Everything else — translation layer
    handle /v1/* {
        reverse_proxy localhost:8100
    }

    # Aggregated models endpoint — translation layer merges all backends
    handle /v1/models {
        reverse_proxy localhost:8100
    }

    # Health — aggregated
    handle /health {
        reverse_proxy localhost:8100
    }
}
```

## Parent Image

Derives from the Vast vLLM image at `external/vllm/`. That image:
- Base: `vllm/vllm-openai:v0.13.0` (official upstream, pre-compiled)
- Adds Vast.ai tooling via `convert-non-vast-image.sh` (supervisor, portal, caddy, jupyter, etc.)
- vLLM venv at `/venv/main/` (conda-like env from upstream image)
- Supervisor services: `vllm`, `ray`, `model-ui`
- Boot env: `05-vllm-env.sh` sets `VLLM_MODEL`, `RAY_ARGS`, `AUTO_PARALLEL`, `PORTAL_CONFIG`
- vLLM startup script waits for Ray, auto-applies tensor parallelism based on GPU count
- Model UI is a lightweight web chat interface (not needed in agent image — disable or repurpose)

## What This Image Adds

1. **ComfyUI** (API mode) with custom nodes:
   - ACE-Step (native, for music gen)
   - TTS-Audio-Suite (Chatterbox, Kokoro, F5-TTS, etc.)
   - Standard image/video nodes (Flux, SDXL, HunyuanVideo, WAN, etc.)

2. **Whisper** (OpenAI-compatible STT server)

3. **Translation layer** (FastAPI service)

4. **Inner Caddy** gateway unifying all services on one port

5. **Template workflows** for each modality

6. **SUPERVISOR_AUTOSTART** support (reuse from AIO Studio — `ROOT/etc/vast_boot.d/60-supervisor-autostart.sh`)

## Key Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `VLLM_MODEL` | (required) | LLM model to serve (e.g. `NousResearch/Hermes-3-Llama-3.1-8B`) |
| `VLLM_ARGS` | | Additional vLLM args |
| `AGENT_BACKENDS` | `vllm,comfyui,whisper` | Comma-separated backends to enable |
| `COMFYUI_ARGS` | `--listen 127.0.0.1 --port 18188 --disable-auto-launch` | ComfyUI args |
| `GATEWAY_PORT` | `3000` | Unified gateway port |
| `SUPERVISOR_AUTOSTART` | `vllm,ray,comfyui,whisper,agent-gateway,agent-translator` | Services to start on boot |

## Build Pattern

```dockerfile
# Derive from the Vast vLLM image (not the upstream — we need Vast tooling)
ARG VLLM_IMAGE=vastai/vllm:v0.13.0-cu128
FROM ${VLLM_IMAGE}

ENV UV_TORCH_BACKEND=cu128

# ComfyUI + custom nodes (use create_app_venv pattern from AIO Studio)
# Translation layer (FastAPI)
# Whisper
# Inner Caddy gateway
# Template workflows
# Supervisor configs

COPY ./ROOT /
```

Uses `--build-context` for ComfyUI provisioner if needed (same pattern as AIO Studio).

## Relationship to AIO Studio

This image shares several patterns with `derivatives/pytorch/derivatives/aio-studio/`:
- `create_app_venv` helper (isolated venvs sharing torch via .pth + copied dist-info)
- `SUPERVISOR_AUTOSTART` boot mechanism (`60-supervisor-autostart.sh`)
- ComfyUI installation pattern (clone, install deps, custom nodes)
- Whisper installation pattern

Key differences:
- Base is vLLM image (not multi-torch pytorch image) — only one torch version
- No web UIs for individual apps (headless/API-only)
- Translation layer is novel to this image
- Inner Caddy gateway is novel (AIO uses outer Caddy with portal)
- Focused on agent consumption, not interactive use

## ComfyUI Custom Nodes Required

| Node | Purpose | Source |
|------|---------|--------|
| ComfyUI-Manager | Node management | `Comfy-Org/ComfyUI-Manager` |
| ACE-Step | Music generation (native support) | Built-in or `ace-step/ACE-Step-ComfyUI` |
| TTS-Audio-Suite | TTS (Chatterbox, Kokoro, F5-TTS, Qwen3-TTS) | `diodiogod/TTS-Audio-Suite` |

## Implementation Order

1. Dockerfile — base structure, ComfyUI + Whisper install
2. Translation layer — FastAPI service with workflow template engine
3. Template workflows — one per modality, exported from ComfyUI
4. Caddy gateway — Caddyfile + supervisor config
5. Boot scripts — env setup, autostart
6. Testing — verify each endpoint works end-to-end

## Files in This Directory

```
agent-api/
  CLAUDE.md              — This file
  Dockerfile             — Image build
  ROOT/
    etc/
      supervisor/conf.d/ — Supervisor service configs
      vast_boot.d/       — Boot-time environment and setup
    opt/
      supervisor-scripts/ — Service startup scripts
      agent-api/
        translator/      — FastAPI translation layer
        workflows/       — ComfyUI template workflows (JSON)
        Caddyfile        — Inner gateway config
```
