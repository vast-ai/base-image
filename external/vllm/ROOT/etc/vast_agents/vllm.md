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

**Which model is served is set by `VLLM_MODEL`** (falls back to `MODEL_NAME`). The
service **refuses to start with no model set** — so if `vllm` is down, check that
first (`/var/log/portal/vllm.log` will say *"Refusing to start … VLLM_MODEL not
set"*). To set or change the model, persist the var and restart the service
(base.md §8):
```
echo 'VLLM_MODEL="Qwen/Qwen3-8B"' >> ${WORKSPACE}/.env
supervisorctl restart vllm
```

**Extra `vllm serve` flags:** put simple ones in **`VLLM_ARGS`**; for anything awkward
to express as an env var, write them to **`/etc/vllm-args.conf`** (its contents are
appended to the launch command). Multi-GPU is automatic — **`AUTO_PARALLEL`** (default
`true`, alias `USE_ALL_GPUS`) adds `--tensor-parallel-size $GPU_COUNT` unless you have
already set a `tensor-parallel-size`/`data-parallel-size` in `VLLM_ARGS`.

### What else runs

- **`ray`** — a local Ray head + dashboard (portal label **"Ray Dashboard"**); `vllm`
  waits for it before starting.
- **`model-ui`** — a lightweight chat web UI (portal label **"Model UI"**) wired to the
  vLLM endpoint for quick manual testing.
- `vllm` also waits for provisioning to finish (`/.provisioning`) before serving, so
  during boot it may be intentionally down — check that flag before assuming a fault
  (base.md §10).

### The Python environment

`vllm` and its dependencies live in the default venv **`/venv/main`** (active in login
shells; a **"vLLM"** Jupyter kernel is also registered). The `vllm` CLI runs through
that venv, so you can `uv pip install <pkg>` (e.g. a newer `transformers`) into
`/venv/main` and the server picks it up on restart. The wheel bundles its own CUDA
runtime — for a GPU that needs a newer CUDA than this build targets (e.g. Blackwell),
see base.md §12. `VLLM_CACHE_ROOT` defaults to `${WORKSPACE}/.vllm_cache`.
