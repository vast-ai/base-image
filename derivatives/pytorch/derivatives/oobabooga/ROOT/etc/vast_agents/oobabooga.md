## Text Generation WebUI — oobabooga (this image)

The base image plus a preinstalled **oobabooga Text Generation WebUI** for running LLMs.
Everything in base.md (supervisor, Caddy auth edge, ports, storage, GPU/CUDA,
provisioning) applies unchanged — this file covers only what oobabooga adds. Unlike the
vLLM/SGLang images there is **no Model UI and no Ray service**: the chat UI and the API
are served by the *same* `server.py` process, and the model is chosen interactively (or
per request) rather than fixed at boot.

### The service and its endpoints

Runs as the supervisor service **`oobabooga`**, which launches `server.py --api` and
binds **two** loopback ports, both fronted by the token-authed Caddy edge:

- the **chat WebUI** on `127.0.0.1:17860` (portal label **"Text Generation WebUI"**);
- an **OpenAI-compatible API** on `127.0.0.1:15000` (portal label **"Oobabooga API"**),
  serving `/v1/chat/completions`, `/v1/completions`, `/v1/models` — call it exactly like
  the OpenAI API with the instance token (base.md §5).

Get the externally callable `base_url` + auth from the capability manifest rather than
guessing the port (base.md §9):
```
curl -s http://localhost:11111/capabilities/endpoints   # base_url, capabilities, auth
```

### Models

The image **bakes no model**, but a tiny default (`facebook_galactica-125m`) loads out of
the box so the API answers immediately — it is a placeholder, not a useful model. Models
live in **`${DATA_DIRECTORY}text-generation-webui/user_data/models`** (i.e.
`/workspace/...`, so they persist on a volume — base.md §3, §8).

Inspect and switch the loaded model via oobabooga's **internal API** (alongside the
OpenAI surface, same port 15000) or the WebUI **Models** tab:
```
curl -s  http://127.0.0.1:15000/v1/internal/model/info             # currently loaded
curl -s  http://127.0.0.1:15000/v1/internal/model/list             # available in the models dir
curl -sX POST http://127.0.0.1:15000/v1/internal/model/load \
     -H 'Content-Type: application/json' -d '{"model_name":"<name>"}'
```
Download new models by Hugging Face repo id from the WebUI's *Model → Download* field (or
`python download-model.py <user>/<repo>` in the app dir); they land in the models dir
above. To load a specific model **at boot**, pass `--model <name>` via `OOBABOOGA_ARGS`.

### Loaders

oobabooga picks a loader per model *format*, and this image preinstalls the full
accelerator stack (**ExLlamaV3**, **flash-attn**, **llama.cpp** binaries, **xformers** —
amd64/CUDA only). So you can serve:
- **Transformers** checkpoints (FP16/4-bit) — the default, broadest compatibility;
- **GGUF** quantized models via the bundled **llama.cpp**;
- **EXL3** quantized models via **ExLlamaV3**.
The WebUI's Models tab exposes the per-loader options; pick a model in the format you want
and the matching loader is used.

### Tuning (`OOBABOOGA_ARGS`)

Extra `server.py` flags go in **`OOBABOOGA_ARGS`** (e.g. `--model`, `--loader`,
context/GPU options). It is **additive** — the image always passes
`--listen-port 17860 --api --api-port 15000` and appends `OOBABOOGA_ARGS` after it
(argparse takes the last value, so you can override a port by re-passing it).

> **Gotcha:** do **not** add a bare `--listen`. The launcher refuses to start with it,
> because `--listen` makes the UI/API bind `0.0.0.0` and bypass the Caddy auth edge
> (ADR 0004). The app is meant to bind loopback and be reached only through the
> token-authed external ports.
