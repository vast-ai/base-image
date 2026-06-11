## vLLM (this image)

The base image plus a preinstalled **vLLM** OpenAI-compatible inference server.
Everything in base.md (supervisor, Caddy auth edge, ports, storage, GPU/CUDA,
provisioning) applies unchanged — this file covers only what vLLM adds.

### Serving a model

The server runs as the supervisor service **`vllm`** (portal label **"vLLM API"**)
and speaks the OpenAI API. Get its externally callable `base_url` + auth from the
capability manifest rather than guessing the port (base.md §9):
```
curl -s http://localhost:11111/capabilities/endpoints   # base_url, capabilities (chat/completions/embeddings/models), auth
```
Internally it listens on `127.0.0.1:18000`; `/v1` is the OpenAI base and `/docs` the
Swagger UI. Call it exactly like the OpenAI API, with the instance token (base.md §5).

**The model is chosen by `VLLM_MODEL`** (a Hugging Face repo id or a local path). The
service **refuses to start with no model set** — so if `vllm` is down, check that
first (`/var/log/portal/vllm.log` will say *"Refusing to start … VLLM_MODEL not
set"*). It is normally set at instance creation via the template's env, where setting
either `VLLM_MODEL` or `MODEL_NAME` is enough: at boot the two are linked
(`MODEL_NAME` ← `VLLM_MODEL`, then `VLLM_MODEL` ← `MODEL_NAME`) so they end up equal.

To **change the model persistently**, set it where the instance sources env on every
boot, then restart. **Set both `VLLM_MODEL` and `MODEL_NAME`** — that boot-time linking
does *not* re-run on a `supervisorctl restart`, and the two are read by different
services (`vllm` keys off `VLLM_MODEL`, **Model UI keys off `MODEL_NAME`** and won't
start without it). `/etc/environment` survives stop/start; `${WORKSPACE}/.env`
additionally survives recycle/destroy *if* a volume is mounted (base.md §3, §8):
```
printf 'VLLM_MODEL="%s"\nMODEL_NAME="%s"\n' Qwen/Qwen3-8B Qwen/Qwen3-8B >> /etc/environment
supervisorctl restart vllm model-ui     # use ${WORKSPACE}/.env instead, on a volume
```

### Tuning vLLM (env vars and serve args)

vLLM-specific environment variables (all upstream `VLLM_*` vars also apply):

- **`VLLM_MODEL`** — model to serve. Linked to `MODEL_NAME` at boot (set either at
  launch); for a runtime change set **both** (see above).
- **`VLLM_ARGS`** — extra flags appended to `vllm serve`; use for simple values.
- **`/etc/vllm-args.conf`** — a file whose contents are also appended to `vllm serve`.
  Put **awkward-to-quote flags here, especially JSON-valued ones** (`--hf-overrides`,
  `--rope-scaling`, `--override-generation-config '{…}'`) that are painful in an env
  var. Both `VLLM_ARGS` and this file are used together.
- **`AUTO_PARALLEL`** (default `true`, alias `USE_ALL_GPUS`) — adds
  `--tensor-parallel-size $GPU_COUNT` automatically, unless you already set a
  `tensor-parallel-size`/`data-parallel-size` yourself in `VLLM_ARGS`.
- **`VLLM_CACHE_ROOT`** — compile/kernel cache, default `${WORKSPACE}/.vllm_cache`.
- **`RAY_ARGS` / `RAY_ADDRESS`** — local Ray head config, or point at a remote Ray
  cluster instead of the bundled one.

### Companion services

- **`ray`** — a local Ray head + dashboard (portal label **"Ray Dashboard"**); `vllm`
  waits for it before starting.
- **`model-ui`** — the Model UI (below).
- All of these wait for provisioning to finish (`/.provisioning`) before starting, so
  during boot they may be intentionally down — check that flag before assuming a fault
  (base.md §10).

### Model UI

A lightweight single-page web frontend (portal label **"Model UI"**, service
`model-ui`) for exercising the running model from a browser — a Chat tab plus
Image/Video/TTS/STT tabs that appear according to what the model supports; the default
tab is auto-detected from the model name. It is a convenience proxy in front of the
vLLM endpoint, **not a second API** — for programmatic use call `/v1` directly. It only
starts when a model is set. Configure it with `MODEL_UI_*` env vars (e.g.
`MODEL_UI_API_BASE`, `MODEL_UI_DEFAULT_TAB`, `MODEL_UI_*_CAPS`, `MODEL_UI_PROMPT_WRAPPER`).
**Full docs ship in the image at `/opt/model-ui/README.md`** (also under
`tools/model-ui/` in the base-image repo).

### The Python environment

`vllm` and its dependencies live in the default venv **`/venv/main`** (active in login
shells; a **"vLLM"** Jupyter kernel is also registered). The `vllm` CLI runs through
that venv, so you can `uv pip install <pkg>` (e.g. a newer `transformers`) into
`/venv/main` and the server picks it up on restart. The wheel bundles its own CUDA
runtime — for a GPU that needs a newer CUDA than this build targets (e.g. Blackwell),
see base.md §12.
