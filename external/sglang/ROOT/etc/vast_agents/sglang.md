## SGLang (this image)

The base image plus a preinstalled **SGLang** OpenAI-compatible inference server.
Everything in base.md (supervisor, Caddy auth edge, ports, storage, GPU/CUDA,
provisioning) applies unchanged — this file covers only what SGLang adds.

### Serving a model

The server runs as the supervisor service **`sglang`** (portal label **"SGLang API"**)
and speaks the OpenAI API. Get its externally callable `base_url` + auth from the
capability manifest rather than guessing the port (base.md §9):
```
curl -s http://localhost:11111/capabilities/endpoints   # base_url, capabilities (chat/completions/embeddings/models), auth
```
Internally it listens on `127.0.0.1:18000`; `/v1` is the OpenAI base and `/docs` the
Swagger UI. Call it exactly like the OpenAI API, with the instance token (base.md §5).

**The model is chosen by `SGLANG_MODEL`** (a Hugging Face repo id or local path; passed
to `sglang serve --model-path`). The service **refuses to start with no model set** —
so if `sglang` is down, check that first (`/var/log/portal/sglang.log` will say
*"Refusing to start … SGLANG_MODEL not set"*). It is normally set at instance creation
via the template's env, where setting either `SGLANG_MODEL` or `MODEL_NAME` is enough:
at boot the two are linked so they end up equal.

To **change the model persistently**, set it where the instance sources env on every
boot, then restart. **Set both `SGLANG_MODEL` and `MODEL_NAME`** — that boot-time
linking does *not* re-run on a `supervisorctl restart`, and the two are read by
different services (`sglang` keys off `SGLANG_MODEL`, **Model UI keys off `MODEL_NAME`**
and won't start without it). `/etc/environment` survives stop/start; `${WORKSPACE}/.env`
additionally survives recycle/destroy *if* a volume is mounted (base.md §3, §8):
```
printf 'SGLANG_MODEL="%s"\nMODEL_NAME="%s"\n' Qwen/Qwen3-8B Qwen/Qwen3-8B >> /etc/environment
supervisorctl restart sglang model-ui
```

### Tuning SGLang (env vars and serve args)

- **`SGLANG_ARGS`** — extra flags appended to `sglang serve`; for awkward-to-quote /
  JSON flags use the file **`/etc/sglang-args.conf`** instead (its contents are also
  appended).
- **`AUTO_PARALLEL`** (default `true`, alias `USE_ALL_GPUS`) — adds
  `--tensor-parallel-size $GPU_COUNT` automatically, unless you already pinned a
  tensor/data/pipeline parallel size in `SGLANG_ARGS`. Both spellings count
  (`--tp-size` and `--tensor-parallel-size`).
- **Two args are REWRITTEN before launch**, so what you set is not verbatim what
  `sglang serve` receives — check `/var/log/sglang.log` for the real command line
  before concluding a flag was ignored:
  - `--enable-expert-parallel` is *vLLM's* spelling. sglang parses argv strictly, so
    passing it through would exit 2 with `unrecognized arguments` and the server would
    never start. It is removed and replaced with `--ep-size N`. **Setting the flag is
    enough — a size is always emitted.** N is the tensor-parallel size actually in
    effect: what you pinned, else `$GPU_COUNT` when the automatic arg supplied it, else
    1. sglang computes `moe_tp_size = tp_size / ep_size`, so an ep that does not divide
    the tp fails the model load with an arithmetic error naming neither flag; `ep == tp`
    always divides. Your own `--ep-size` is never touched.
  - The auto tensor-parallel arg above.

  Both rewrites read **`/etc/sglang-args.conf` as well as `SGLANG_ARGS`**, so a
  `--tp-size` you put there is what the `--ep-size` is sized from. The conf is still
  appended last and still wins on a duplicate flag.

### Companion services

- **`model-ui`** — a lightweight web frontend (portal label **"Model UI"**) for
  exercising the model from a browser; a convenience proxy in front of the endpoint,
  **not a second API** (call `/v1` directly for programmatic use). Only starts when a
  model is set; configure with `MODEL_UI_*` env vars. Full docs ship at
  **`/opt/model-ui/README.md`** (also `tools/model-ui/` in the base-image repo).
- (SGLang has no Ray service — unlike the vLLM images.)
- Both wait for provisioning (`/.provisioning`) before starting (base.md §10).

### The Python environment

SGLang and its dependencies live in the default venv **`/venv/main`** (active in login
shells; a **"SGLang"** Jupyter kernel is registered), so `uv pip install <pkg>` into
`/venv/main` sticks across a restart. The wheel bundles its own CUDA runtime — for a GPU
needing newer CUDA than this build targets (e.g. Blackwell), see base.md §12.
