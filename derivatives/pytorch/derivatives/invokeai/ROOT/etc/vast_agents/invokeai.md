## InvokeAI (this image)

The PyTorch image plus a preinstalled **InvokeAI** — a node/canvas image-generation studio.
Everything in base.md and pytorch.md applies unchanged (torch is in `/venv/main`); this file
covers what InvokeAI adds. One service, and it is **not** an OpenAI `/v1` endpoint — this is
image generation. Get the externally callable URL + token from the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # the service, with direct_url + state
```

### The app — web UI + REST API on one port (service "invokeai")

Supervisor service **`invokeai`**, launched as `invokeai-web --root "${WORKSPACE}/invokeai"`
(internal `127.0.0.1:19000` by convention — confirm via the manifest's `direct_url`). Unlike
A1111/Forge there is no `--api` toggle and no `*_ARGS`: the **same port serves both the browser
UI and InvokeAI's FastAPI backend, always on**. The OpenAPI schema is the source of truth:
```
GET  /docs                  # full interactive API schema (models, queue, invocations)
GET  /api/v1/...            # app + board/image endpoints
GET  /api/v2/models/        # Model Manager — list/install/scan models
```
Headless generation in InvokeAI is **graph/queue based**: you enqueue a generation graph and
poll the queue for results, rather than calling a single txt2img endpoint. Build the request
from `/docs` (or capture one from the UI) — don't assume an A1111-style `/sdapi` surface; it is
not present here.

### Config, models & provisioning

State lives entirely under the InvokeAI root **`${WORKSPACE}/invokeai`** (so it persists on the
workspace): `invokeai.yaml` config, the model store under `${WORKSPACE}/invokeai/models`, and
outputs. The app uses `/venv/main`. Models are normally added through InvokeAI's own **Model
Manager** (the UI's model tab, or `POST /api/v2/models/install` with a HF repo or URL) rather
than by dropping files — let it register them so they appear in the UI. There is no
image-specific provisioning script; use the base provisioner (`PROVISIONING_SCRIPT`, base.md
§10) for anything declarative. **The service waits for provisioning (`/.provisioning`) to finish
before starting**, so during boot it may be intentionally down — check that flag before
assuming a fault.
