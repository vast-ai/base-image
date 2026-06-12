## ComfyUI (this image)

The PyTorch image plus a preinstalled **ComfyUI** stack. Everything in base.md and
pytorch.md applies unchanged (torch is in `/venv/main`); this file covers what ComfyUI
adds. There are **two** services, and they serve different jobs — an agent should know
both. Neither is an OpenAI `/v1` endpoint; get the externally callable URLs + auth from
the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # both services, with direct_url + state
```

### 1. The ComfyUI app — interactive UI + native API (service "ComfyUI")

The node-based diffusion workflow editor, supervisor service **`comfyui`**, on internal
`127.0.0.1:18188`. Opening its portal entry gives the browser GUI; the **same port also
serves ComfyUI's native HTTP API** for driving it programmatically:
```
GET  /api/system_stats                 # health / device info
POST /prompt        {"prompt": <api-format graph>, "client_id": "..."}   # queue a run
GET  /history/<prompt_id>              # results once complete
GET  /view?filename=...&subfolder=...&type=output                        # fetch an output image
```
This native API is **queue-based and low-level** (you submit a prompt graph and poll, or
watch the `/ws` websocket). Launch flags are in **`COMFYUI_ARGS`** (default
`--disable-auto-launch --enable-cors-header --port 18188`); e.g. add `--enable-manager`
for the built-in manager. Custom nodes are managed by **ComfyUI-Manager** (preinstalled).
Models live under **`${WORKSPACE}/ComfyUI/models/<type>`** (`checkpoints`, `loras`, `vae`,
…); the app runs from `${WORKSPACE}/ComfyUI` in `/venv/main`.

### 2. The API Wrapper — headless one-shot generation (service "API Wrapper")

A FastAPI service (ai-dock/comfyui-api-wrapper, supervisor program `api-wrapper`) on
internal `127.0.0.1:18288`, the **high-level path for automation/serverless**: POST a
workflow + inputs and get the finished media back in one call — no queue polling.
```
POST /generate/sync   <request envelope>     # run now, return outputs in the response
GET  /health                                 # 200 once it can reach ComfyUI
# /docs has the full schema; there is also an async generate path
```
The request envelope is **`{"input": {"workflow_json": <API-format graph>, …}}`**.
Ready-to-run payloads already exist in **`/opt/comfyui-api-wrapper/payloads/`** — one per
saved workflow, generated at startup by converting every GUI workflow in
`${WORKSPACE}/ComfyUI/user/default/workflows` to API format (seeds tokenized as
`__RANDOM_INT__`). The wrapper has its **own venv** at `/opt/comfyui-api-wrapper/.venv`
(not `/venv/main`).

### Using the two together

Design and iterate visually in the **app**; run it headlessly via the **wrapper**. The
bridge is workflow format: ComfyUI graphs are saved as *GUI format*, while both APIs above
take *API format*. ComfyUI's `POST /workflow/convert` converts one, and the wrapper's
startup hook (`convert-workflows.sh`) auto-converts every saved GUI workflow into a wrapper
payload. So the loop is: build a workflow in the GUI → save it → it appears as a runnable
payload under `/opt/comfyui-api-wrapper/payloads/` for `/generate/sync`.

### Models, custom nodes, provisioning

Add models, custom nodes, and workflows declaratively with the **ComfyUI provisioner**
(`PROVISIONING_*` env / a provisioning manifest — see
`derivatives/pytorch/derivatives/comfyui/provisioning/` in the repo, and base.md §10);
models land in `${WORKSPACE}/ComfyUI/models`. **Both services wait for provisioning
(`/.provisioning`) to finish before starting**, so during boot they may be intentionally
down — check that flag before assuming a fault.
