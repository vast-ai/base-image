## Text Generation WebUI — oobabooga (this image)

The base image plus a preinstalled **oobabooga Text Generation WebUI**; base.md applies. A
chat UI and an **OpenAI-compatible API** are served by one `server.py --api` process — get
their `base_url`s + auth from `/capabilities/endpoints` (don't assume ports). Unlike the
vLLM/SGLang images there is **no Model-UI and no Ray service**, and the model is chosen
interactively rather than fixed at boot.

### Models

The image bakes **no model**, but a tiny placeholder (`facebook_galactica-125m`) loads so the
API answers out of the box. Models live in
`${DATA_DIRECTORY}text-generation-webui/user_data/models` (persists on a volume). Manage the
loaded model via oobabooga's **internal API** — *not* in the capability manifest, so call it
directly on the API's loopback port (e.g. `:15000`):
```
curl -s  http://127.0.0.1:15000/v1/internal/model/info               # currently loaded
curl -s  http://127.0.0.1:15000/v1/internal/model/list               # available on disk
curl -sX POST http://127.0.0.1:15000/v1/internal/model/load -d '{"model_name":"<name>"}'
```
…or use the WebUI **Models** tab. Download by Hugging Face repo id from the WebUI (or
`download-model.py`). Pass `--model <name>` via `OOBABOOGA_ARGS` to load one at boot.

### Loaders

The accelerator stack is preinstalled, so a model is served by the loader matching its
**format**: **Transformers** (HF checkpoints), **GGUF** (llama.cpp), or **EXL3** (ExLlamaV3).

### Gotcha

`OOBABOOGA_ARGS` is **additive** (appended after the baked `--api`/port flags). **Do not add a
bare `--listen`** — it binds the UI/API on `0.0.0.0`, bypassing the Caddy auth edge, so the
launcher refuses to start (the loopback-behind-Caddy invariant).
