## Wan2GP (this image)

The PyTorch image plus a preinstalled **Wan2GP** (upstream `deepbeepmeep/Wan2GP`) — a low-VRAM
**video generation** UI (text-to-video / image-to-video with Wan-family models). Everything in
base.md and pytorch.md applies unchanged (torch is in `/venv/main`); this file covers what Wan2GP
adds. One service, and it is **not** an OpenAI `/v1` endpoint. Get the externally callable URL +
token from the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # the service, with direct_url + state
```

### The app — a Gradio UI (service "wan2gp")

Supervisor service **`wan2gp`** (`python wgp.py`), internal `127.0.0.1:7860`. It is a **Gradio**
app and is **UI-first** — pick a model and generate from the browser. Gradio does expose a
programmatic API (the "Use via API" link at the bottom of the UI, served under `/gradio_api/`),
but there is no curated REST surface here, so for most tasks drive the UI rather than scripting
it. The port is set by **`WAN2GP_PORT`** (default `7860`) and extra launch flags by
**`WAN2GP_ARGS`**.

### Models, outputs & provisioning

The app lives at `${WORKSPACE}/Wan2GP` and runs in `/venv/main`; generated videos land under
that app directory's own outputs (`${WORKSPACE}/Wan2GP/...`). **Model weights download on first
use** and large video runs are slow — expected, not a hang. Add anything declaratively with the
base provisioner (`PROVISIONING_SCRIPT`, base.md §10). **The service waits for provisioning
(`/.provisioning`) to finish before starting**, so during boot it may be intentionally down —
check that flag before assuming a fault. (This image is built amd64-only — it needs
onnxruntime-gpu, which ships x86_64 wheels only.)
