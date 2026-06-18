## FluxGym (this image)

The PyTorch image plus a preinstalled **FluxGym** (upstream `cocktailpeanut/fluxgym`) — a LoRA
**training** UI for FLUX models that wraps Kohya `sd-scripts`. Everything in base.md and
pytorch.md applies unchanged (torch is in `/venv/main`); this file covers what FluxGym adds. It
is a training tool, **not** an inference/OpenAI endpoint — the deliverable is a trained LoRA
file, not an API response. Get the externally callable URL + token from the manifest
(base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # the service, with direct_url + state
```

### The app — a Gradio UI (service "fluxgym")

Supervisor service **`fluxgym`** (`python app.py`), internal `127.0.0.1:17860` (port via
**`FLUXGYM_PORT`**). It is **UI-driven and has no API**: you add images + captions, set
parameters, and click train. Under the hood FluxGym **generates a `train.sh` + `dataset.toml`
and runs Kohya `sd-scripts`** (`accelerate launch sd-scripts/flux_train_network.py …`).

### Driving a run headlessly

There's no FluxGym API, but the Kohya trainer it calls is fully present, so an agent can train
without the UI. From `${WORKSPACE}/fluxgym` in `/venv/main`, either:
- re-run a config the UI already produced: `bash outputs/<name>/train.sh`, or
- invoke Kohya directly: `accelerate launch sd-scripts/flux_train_network.py <args>` (`sd-scripts`
  lives at `${WORKSPACE}/fluxgym/sd-scripts`).

### Data, models, outputs

All under `${WORKSPACE}/fluxgym` (persisted on the workspace):
- **Datasets** — `datasets/<name>/`: training images + a matching `.txt` caption per image.
- **Outputs** — `outputs/<name>/`: the trained **LoRA `.safetensors`**, plus the generated
  `train.sh` / `dataset.toml`, sample images, and logs. This is what you pull back.
- **Base weights** — `models/` (unet/clip/vae); **downloaded on first run**, not baked in, so the
  first training start is slow. Gated FLUX-dev needs a Hugging Face token — enter it in the UI, or
  export `HF_TOKEN` before a headless Kohya run (nothing in this image pre-sets it).

The app runs in `/venv/main` and **waits for provisioning (`/.provisioning`) to finish before
starting**, so during boot it may be intentionally down — check that flag before assuming a fault.
