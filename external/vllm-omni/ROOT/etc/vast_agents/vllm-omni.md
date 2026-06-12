## vLLM-Omni (this image)

The base image plus a preinstalled **vLLM-Omni** server — vLLM's multimodal variant,
OpenAI-compatible. Everything in base.md (supervisor, Caddy auth edge, ports, storage,
GPU/CUDA, provisioning) applies unchanged; this file covers only what vLLM-Omni adds.

### Serving a model

The server runs as the supervisor service **`vllm-omni`** (portal label **"vLLM-Omni
API"**) and speaks the OpenAI API. Get its externally callable `base_url` + auth from
the capability manifest rather than guessing the port (base.md §9):
```
curl -s http://localhost:11111/capabilities/endpoints   # base_url, capabilities (chat/completions/models), auth
```
Internally it listens on `127.0.0.1:18000`; `/v1` is the OpenAI base and `/docs` the
Swagger UI. It is **multimodal** — depending on the model, chat requests accept image
and audio inputs, and the OpenAI image/video/audio routes may be available (no text
embeddings, unlike plain vLLM). Call it like the OpenAI API with the instance token
(base.md §5).

**The model is chosen by `VLLM_MODEL`** (a Hugging Face repo id or local path). The
service **refuses to start with no model set** — so if `vllm-omni` is down, check that
first (`/var/log/portal/vllm-omni.log` will say *"Refusing to start … VLLM_MODEL not
set"*). It is normally set at instance creation via the template's env, where setting
either `VLLM_MODEL` or `MODEL_NAME` is enough: at boot the two are linked so they end
up equal.

To **change the model persistently**, set it where the instance sources env on every
boot, then restart. **Set both `VLLM_MODEL` and `MODEL_NAME`** — that boot-time linking
does *not* re-run on a `supervisorctl restart`, and the two are read by different
services (`vllm-omni` keys off `VLLM_MODEL`, **Model UI keys off `MODEL_NAME`** and
won't start without it). `/etc/environment` survives stop/start; `${WORKSPACE}/.env`
additionally survives recycle/destroy *if* a volume is mounted (base.md §3, §8):
```
printf 'VLLM_MODEL="%s"\nMODEL_NAME="%s"\n' Qwen/Qwen3-Omni-7B Qwen/Qwen3-Omni-7B >> /etc/environment
supervisorctl restart vllm-omni model-ui
```

### Tuning vLLM-Omni (env vars and serve args)

- **`VLLM_ARGS`** — extra flags appended to `vllm serve`; for awkward-to-quote / JSON
  flags (`--hf-overrides`, `--rope-scaling`, `--override-generation-config '{…}'`) use
  the file **`/etc/vllm-args.conf`** instead (its contents are also appended).
- **`AUTO_PARALLEL`** (default `true`, alias `USE_ALL_GPUS`) — adds
  `--tensor-parallel-size $GPU_COUNT` unless you set parallelism yourself in
  `VLLM_ARGS`; if `VLLM_ARGS` enables `--enable-expert-parallel` it uses
  `--tensor-parallel-size 1 --data-parallel-size $GPU_COUNT` instead.
- **`VLLM_CACHE_ROOT`** — compile/kernel cache, default `${WORKSPACE}/.vllm_cache`.
- **`RAY_ARGS` / `RAY_ADDRESS`** — local Ray head config, or point at a remote cluster.
- **Crash watchdog:** vLLM-Omni's engine core can die while the HTTP server stays up
  (Supervisor then sees a live process and never restarts). A background watchdog tails
  the log and kills the service on fatal engine errors so Supervisor restarts it.
  Disable with **`VLLM_WATCHDOG=false`**; override the regex with **`VLLM_CRASH_PATTERN`**.

### Companion services

- **`ray`** — a local Ray head + dashboard (portal label **"Ray Dashboard"**);
  `vllm-omni` waits for it before starting.
- **`model-ui`** — a lightweight web frontend (portal label **"Model UI"**) for
  exercising the model from a browser; a convenience proxy in front of the endpoint,
  **not a second API** (call `/v1` directly for programmatic use). Only starts when a
  model is set; configure with `MODEL_UI_*` env vars. Full docs ship at
  **`/opt/model-ui/README.md`** (also `tools/model-ui/` in the base-image repo).
- All of these wait for provisioning (`/.provisioning`) before starting (base.md §10).

### The Python environment

`vllm` and its dependencies live in the default venv **`/venv/main`** (active in login
shells; a **"vLLM-Omni"** Jupyter kernel is registered). The `vllm` CLI runs through
that venv, so `uv pip install <pkg>` into `/venv/main` sticks across a restart. The
wheel bundles its own CUDA runtime — for a GPU needing newer CUDA than this build
targets, see base.md §12.
