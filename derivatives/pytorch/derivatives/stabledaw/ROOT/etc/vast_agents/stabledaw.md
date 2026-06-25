# StableDAW

StableDAW (upstream: [gantasmo/theDAW](https://github.com/gantasmo/theDAW)) is an
all-in-one, browser-based, AI-powered digital audio workstation. The core is a
PyTorch Stable Audio 3 diffusion pipeline (`stable_audio_3/`); a FastAPI backend
wraps it with a generation job queue, FFmpeg audio processing, a library, and
analysis/MIDI/notation modules.

## How it runs on this image

- One process under supervisor (`stabledaw.sh`) launches `/opt/stabledaw-serve.py`,
  which serves the **built React SPA at `/`** and the **REST API under `/api/*`**
  from a single FastAPI app.
- It binds **`127.0.0.1:18600`** (loopback). Reach it through the Instance Portal's
  Caddy proxy on external port **8600** — never hit `0.0.0.0`. Override the bind
  with `STABLEDAW_HOST` / `STABLEDAW_PORT`.
- App source lives at **`/opt/stabledaw`** (cloned at build, pinned by
  `STABLEDAW_REF`). The built frontend is at `/opt/stabledaw/frontend/dist`.

## Models and data

- **Local-only by default**: nothing downloads at startup. A model loads on the
  first generation that needs it, resolving local folders, then the Hugging Face
  cache, with a one-time download only after it is explicitly allowed in the UI.
- The Medium model needs **~8 GB VRAM**; use the `small` model or a shorter
  duration if you hit OOM.
- The library DB and rendered audio live under `/opt/stabledaw/data/` (overlay —
  persists across stop/start, **not** on the shared `$WORKSPACE` volume).

## Common tasks

- Health check: `curl http://127.0.0.1:18600/api/health`.
- Logs: `supervisorctl tail -f stabledaw`.
- Restart: `supervisorctl restart stabledaw` (the in-app Restart button also works).

## Caveats

- **flash-attn is not installed** on this Linux image (upstream declares it
  Windows-only). Generation works; the Medium model is just unaccelerated.
- The optional Magenta RealTime sidecar (`sidecars/magenta-rt2-nvidia`, a git
  submodule) is **not** vendored in this image; Magenta entries in the model
  picker will be unavailable unless installed separately.
