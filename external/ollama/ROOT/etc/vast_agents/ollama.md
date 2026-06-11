## Ollama (this image)

The base image plus a preinstalled **Ollama** model server. Everything in base.md
(supervisor, Caddy auth edge, ports, storage, GPU/CUDA, provisioning) applies
unchanged — this file covers only what Ollama adds.

### The server and its endpoints

Ollama runs as the supervisor service **`ollama`** (portal label **"Ollama API"**) and
exposes **two** APIs on the same port: Ollama's **native REST API** (`/api/*` —
`/api/generate`, `/api/chat`, `/api/tags`, `/api/pull`, …) and an **OpenAI-compatible**
surface at **`/v1`**. Get the externally callable `base_url` + auth from the capability
manifest rather than guessing the port (base.md §9):
```
curl -s http://localhost:11111/capabilities/endpoints   # base_url (/v1), capabilities (chat/completions/embeddings/models), auth
```
Internally it listens on `127.0.0.1:21434` (Caddy proxies external `11434` → `21434`).
Call `/v1` like the OpenAI API with the instance token (base.md §5); the client picks
the model per request, so **any pulled model can be served** — there is no single
"loaded" model as with vLLM.

### Managing models

The `ollama` CLI is the main tool, and models persist in
`OLLAMA_MODELS` (default `${WORKSPACE}/ollama/models`, so they survive on a volume):
```
ollama list                 # what's pulled
ollama pull qwen3:8b        # fetch a model (then it's immediately servable via /v1 or /api)
ollama run  qwen3:8b        # pull + interactive chat
ollama rm   qwen3:8b
```

**`OLLAMA_MODEL`** names a model to **pull automatically at boot** (the service blocks
on the pull and exits if it fails). Unlike vLLM, the server still starts with no model
set — it just has nothing pulled yet. To change the boot-time model persistently, set
it where the instance sources env on every boot, then restart. **Set both
`OLLAMA_MODEL` and `MODEL_NAME`** — the boot-time linking between them does *not* re-run
on a `supervisorctl restart`, and **Model UI keys off `MODEL_NAME`** (won't start
without it). `/etc/environment` survives stop/start; `${WORKSPACE}/.env` additionally
survives recycle/destroy *if* a volume is mounted (base.md §3, §8):
```
printf 'OLLAMA_MODEL="%s"\nMODEL_NAME="%s"\n' qwen3:8b qwen3:8b >> /etc/environment
supervisorctl restart ollama model-ui
```
(For a model you just want available now, a plain `ollama pull` is enough — no restart
needed.)

### Other env vars and companion service

- **`OLLAMA_HOST`** — bind address, default `0.0.0.0:21434` (keep the `21434` port so
  the Caddy mapping works). **`OLLAMA_ARGS`** — extra flags for `ollama serve`.
- **`OLLAMA_MODELS`** — model store, default `${WORKSPACE}/ollama/models`.
- **`model-ui`** — a lightweight web frontend (portal label **"Model UI"**) for
  exercising a model from a browser; a convenience proxy in front of the Ollama
  endpoint, **not a second API** (call `/v1` or `/api` directly for programmatic use).
  Only starts when `MODEL_NAME` is set; configure with `MODEL_UI_*` env vars. Full docs
  ship at **`/opt/model-ui/README.md`** (also `tools/model-ui/` in the base-image repo).
- Both wait for provisioning (`/.provisioning`) before starting (base.md §10). Ollama
  has no Ray service.
