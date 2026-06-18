## Wan2GP (this image)

The PyTorch image plus a preinstalled **Wan2GP / WanGP** (upstream `deepbeepmeep/Wan2GP`) — a
low-VRAM **video generation** app (text-to-video / image-to-video with Wan-family models).
Everything in base.md and pytorch.md applies unchanged (torch is in `/venv/main`); this file
covers what Wan2GP adds. It is **not** an OpenAI `/v1` endpoint. Get the externally callable URL
+ token for the UI from the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # the service, with direct_url + state
```

### Interactive — the Gradio UI (service "wan2gp")

Supervisor service **`wan2gp`** (`python wgp.py`), internal `127.0.0.1:7860` — pick a model and
generate from the browser. Port is set by **`WAN2GP_PORT`** (default `7860`), extra launch flags
by **`WAN2GP_ARGS`**. This is the only service running by default.

### Programmatic — Python API + MCP (prefer this for automation)

WanGP ships a real agent-facing API; **don't assume it's UI-only.** Two paths, both documented
on the box at **`${WORKSPACE}/Wan2GP/docs/API.md`** (read it — it's version-matched to this image
and is the source of truth):

- **In-process Python API** (`shared/api.py`). Run it with `/venv/main` python from the app dir:
  ```python
  from shared.api import init
  session = init(root="${WORKSPACE}/Wan2GP")
  print(session.list_model_metadata())          # discover available models/features
  settings = session.get_default_settings("<model_type>")   # then set prompt/resolution/etc
  settings["prompt"] = "a neon train entering a rainy station"
  job = session.submit_task(settings)
  result = job.result()                          # result.generated_files -> the video(s)
  ```
  Settings are *WanGP Settings* dicts; the easiest way to get a known-good one is the UI's
  **"Export Settings"** button, or `get_model_schema(model_type)` / `get_default_settings(...)`.
- **MCP server** — WanGP includes an MCP server with discovery functions (it can enumerate the
  available generative models and features for an agent). It is **not started by default here**
  (only the Gradio service runs); launch it per `docs/API.md` if your agent speaks MCP.

### Models, outputs & provisioning

The app lives at `${WORKSPACE}/Wan2GP` and runs in `/venv/main`; generated videos land under that
app directory's outputs (`${WORKSPACE}/Wan2GP/...`, also surfaced via `result.generated_files`).
**Model weights download on first use** and large runs are slow — expected, not a hang. Add
anything declaratively with the base provisioner (`PROVISIONING_SCRIPT`, base.md §10). **The
service waits for provisioning (`/.provisioning`) to finish before starting**, so during boot it
may be intentionally down — check that flag before assuming a fault. (This image is built
amd64-only — it needs onnxruntime-gpu, which ships x86_64 wheels only.)
