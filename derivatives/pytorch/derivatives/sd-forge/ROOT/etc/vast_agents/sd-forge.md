## Stable Diffusion WebUI Forge (this image)

The PyTorch image plus a preinstalled **SD WebUI Forge** — an AUTOMATIC1111 fork tuned for
lower VRAM use and for Flux. Everything in base.md and pytorch.md applies unchanged (torch is
in `/venv/main`); this file covers what Forge adds. One service, and it is **not** an OpenAI
`/v1` endpoint — this is image generation. Get the externally callable URL + token from the
manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # the service, with direct_url + state
```

### The WebUI — and its REST API (service "forge")

Supervisor service **`forge`**, internal `127.0.0.1:17860`. Opening its portal entry gives the
browser GUI. Launch flags live in **`FORGE_ARGS`** (default `--port 17860`); set it on the
instance and restart the `forge` service to change them.

Forge keeps A1111's API contract, and **the REST API is OFF by default.** Add `--api` to
`FORGE_ARGS` (e.g. `FORGE_ARGS="--port 17860 --api"`) and restart to expose it on the same port
(same authed portal path):
```
GET  /sdapi/v1/sd-models                                  # list installed checkpoints
POST /sdapi/v1/options  {"sd_model_checkpoint": "<name>"} # switch the active checkpoint
POST /sdapi/v1/txt2img  {"prompt": "...", "steps": 20}    # generate; PNGs returned base64 in .images
POST /sdapi/v1/img2img  {...}
GET  /docs                                                # full OpenAPI schema (only when --api is on)
```
Without `--api`, only the GUI is served.

### Models & provisioning

The app runs from `${WORKSPACE}/stable-diffusion-webui-forge` in `/venv/main`; models live
under `${WORKSPACE}/stable-diffusion-webui-forge/models/` — `Stable-diffusion/` (checkpoints),
`VAE/`, `text_encoder/` (CLIP/T5 for Flux), `Lora/`. Add models declaratively with a
**provisioning script** (base.md §10): two are shipped —
`derivatives/pytorch/derivatives/sd-forge/provisioning_scripts/default.sh` (SD baseline) and
`flux.sh` (sets up Flux — pulls gated FLUX.1-dev with a valid `HF_TOKEN`, else open
FLUX.1-schnell, plus the CLIP/T5 encoders). Both take `HF_MODELS` / `CIVITAI_MODELS` /
`WGET_DOWNLOADS` (semicolon-separated `URL|PATH` pairs) and `HF_TOKEN` / `CIVITAI_TOKEN`.
**The service waits for provisioning (`/.provisioning`) to finish before starting**, so during
boot it may be intentionally down — check that flag before assuming a fault.
