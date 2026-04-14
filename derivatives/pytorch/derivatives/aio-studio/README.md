# AIO Studio Image

An all-in-one creative AI studio derived from the Vast.ai [PyTorch image](../../README.md). This image bundles eight applications and a GPU-accelerated remote desktop with Blender, covering image generation, video generation, music creation, voice synthesis, transcription, model training, and 3D rendering — all managed via Supervisor with on-demand start/stop.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../../../README.md).

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/aio-studio/tags).

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `aio-studio-cuda-12.9-py312`). This does not mean you need that exact CUDA version on the host.

### Minor Version Compatibility

NVIDIA's [minor version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) guarantees that an application built with any CUDA toolkit release within a major version family will run on a system with a driver from the same major family. In practice:

- A `cuda-12.9` image will run on any machine with a CUDA 12.x driver (driver >= 525).
- A `cuda-13.1` image will run on any machine with a CUDA 13.x driver (driver >= 580).
- The 12.x and 13.x families are separate — a `cuda-13.1` image will not work with a 12.x driver under minor version compatibility alone.

**PTX caveat:** Applications that compile device code to PTX rather than pre-compiled SASS for the target architecture will not work on older drivers within the same major family.

Choose the CUDA variant that matches your driver's major version. Within that family, any minor version will work.

### Forward Compatibility

[Forward compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) allows newer CUDA toolkit versions to run on older drivers. It is only available on **datacenter GPUs** (and select NGC Server Ready RTX cards). All of our images include the CUDA Compatibility Package (`cuda-compat`) to support this.

For example, with forward compatibility a `cuda-12.9` image could run on a datacenter machine with a CUDA 12.1 driver, or a `cuda-13.1` image could run with a CUDA 12.x driver. Consumer GPUs do not support forward compatibility and require a driver that natively supports the image's CUDA version.

## Included Applications

| Application | Description | Port | Supervisor Service |
|------------|-------------|------|-------------------|
| Desktop (KDE + [Blender](https://www.blender.org/)) | GPU-accelerated remote desktop via WebRTC | 16100 | `desktop` |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | Node-based image/video generation | 18188 | `comfyui` |
| [SD Forge](https://github.com/Haoming02/sd-webui-forge-classic) | Stable Diffusion WebUI (classic) | 17860 | `forge` |
| [Wan2GP](https://github.com/deepbeepmeep/Wan2GP) | Video generation (Wan 2.x) | 7861 | `wan2gp` |
| [ACE Step 1.5](https://github.com/ace-step/ACE-Step-1.5) | AI music generation | 3000 | `ace-step` |
| [Voicebox](https://github.com/jamiepine/voicebox) | Text-to-speech synthesis | 17493 | `voicebox` |
| [Whisper WebUI](https://github.com/jhj0517/Whisper-WebUI) | Speech-to-text transcription | 7862 | `whisper-webui` |
| [Ostris AI Toolkit](https://github.com/ostris/ai-toolkit) | LoRA/model training | 8675 | `ai-toolkit` |
| [Unsloth Studio](https://github.com/unslothai/unsloth) | LLM fine-tuning & serving | 18888 | `unsloth-studio` |

All services are set to `autostart=false`. Start and stop them on demand via the **Supervisor** tab in Instance Portal or the command line.

## Python Environments

| Venv | PyTorch | Used By |
|------|---------|---------|
| `/venv/main` | 2.10.0 | ComfyUI, SD Forge, ACE Step, Unsloth Studio |
| `/venv/torch-2.9.1` | 2.9.1 | Voicebox |
| `/venv/torch-2.7.1` | 2.7.1 | Ostris AI Toolkit, Wan2GP, Whisper WebUI |

Each application (except Unsloth Studio) gets its own isolated venv under `/venv/<app>` that shares only torch from its base via `.pth` files. This prevents dependency conflicts between apps while avoiding multi-GB torch duplication.

## Shared Models

ComfyUI and SD Forge share model directories via symlinks. Models placed in either location are visible to both applications:

| ComfyUI Path | Forge Path |
|-------------|-----------|
| `models/checkpoints` | `models/Stable-diffusion` |
| `models/loras` | `models/Lora` |
| `models/vae` | `models/VAE` |
| `models/controlnet` | `models/ControlNet` |
| `models/embeddings` | `embeddings` |
| `models/upscale_models` | `models/ESRGAN` |

## Unsloth Studio: Fine-Tuning & Serving

Unsloth Studio is not just for training — it can also **serve fine-tuned models** via its built-in Chat tab. Under the hood, Studio runs a llama.cpp server and exposes an **OpenAI-compatible API** at the same port:

- **Chat UI:** Use the Chat tab in the Studio web interface
- **OpenAI-compatible API:** `POST http://<host>:18888/v1/chat/completions`
- **Model listing:** `GET http://<host>:18888/v1/models`

This means you can fine-tune a model and immediately serve it for inference — all from the same application. External tools like Open WebUI or SillyTavern can connect to the `/v1/` endpoint.

> **Note:** The `/v1/` API endpoints require Studio authentication (JWT). Authenticate first via `/api/auth/`.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Workspace directory |
| `COMFYUI_ARGS` | `--disable-auto-launch --enable-cors-header --port 18188` | ComfyUI startup arguments |
| `FORGE_ARGS` | `--port 17860` | SD Forge startup arguments |
| `VOICEBOX_ARGS` | `--host 127.0.0.1 --port 17493` | Voicebox startup arguments |
| `UNSLOTH_STUDIO_ARGS` | `--host 127.0.0.1 --port 18888` | Unsloth Studio startup arguments |
| `AI_TOOLKIT_START_CMD` | `npm run start` | AI Toolkit start command |
| `ACESTEP_LM_MODEL_PATH` | `acestep-5Hz-lm-4B` | ACE Step language model path |
| `WAN2GP_PORT` | `7861` | Wan2GP server port |
| `WHISPER_UI_ARGS` | `--whisper_type whisper --server_port 7862` | Whisper WebUI startup arguments |
| `SUPERVISOR_AUTOSTART` | (none) | Comma-separated services to auto-start on boot |
| `DISPLAY_SIZEW` | `1920` | Desktop resolution width |
| `DISPLAY_SIZEH` | `1080` | Desktop resolution height |
| `SELKIES_ENCODER` | `x264enc` | Desktop streaming encoder |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |
| `ENABLE_HTTPS` | (none) | Set to `true` for HTTPS access (strongly recommended) |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Desktop (Selkies) | 6100 | 16100 |
| VNC | 5900 | 5900 |
| ComfyUI | 8188 | 18188 |
| SD Forge | 7860 | 17860 |
| Wan2GP | 7861 | 7861 |
| AI Toolkit | 18675 | 8675 |
| ACE Step | 13000 | 3000 |
| Unsloth Studio | 8888 | 18888 |
| Voicebox | 7493 | 17493 |
| Whisper WebUI | 17862 | 7862 |
| Jupyter | 8080 | 8080 |

### Service Management

All applications are managed via Supervisor. No services auto-start — you choose what to run:

```bash
# Start an application
supervisorctl start comfyui

# Stop an application
supervisorctl stop comfyui

# Check status of all services
supervisorctl status

# View live logs
supervisorctl tail -f comfyui

# Start multiple services at once
supervisorctl start comfyui voicebox
```

You can also manage services through the **Supervisor** tab in Instance Portal.

## Building From Source

```bash
cd base-image/derivatives/pytorch/derivatives/aio-studio

docker buildx build -t yournamespace/aio-studio .
```

All build arguments have sensible defaults. Override as needed:

```bash
docker buildx build \
    --build-arg PYTORCH_BASE=robatvastai/pytorch:multi-210-291-271-cu128 \
    --build-arg COMFYUI_REF=v0.18.1 \
    -t yournamespace/aio-studio .
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `PYTORCH_BASE` | `robatvastai/pytorch:multi-210-291-271-cu128` | PyTorch base image (multi-torch: 2.10, 2.9.1, 2.7.1) |
| `COMFYUI_REPO` | `https://github.com/Comfy-Org/ComfyUI` | ComfyUI repository |
| `COMFYUI_REF` | `v0.18.1` | ComfyUI git ref |
| `FORGE_REPO` | `https://github.com/Haoming02/sd-webui-forge-classic` | SD Forge repository |
| `FORGE_REF` | `a90af56` | SD Forge git ref |
| `WAN2GP_REPO` | `https://github.com/deepbeepmeep/Wan2GP` | Wan2GP repository |
| `WAN2GP_REF` | `e2b56fe` | Wan2GP git ref |
| `ACE_STEP_REPO` | `https://github.com/ace-step/ACE-Step-1.5` | ACE Step repository |
| `ACE_STEP_REF` | `v0.1.4` | ACE Step git ref |
| `ACE_STEP_UI_REPO` | `https://github.com/fspecii/ace-step-ui` | ACE Step UI repository |
| `ACE_STEP_UI_REF` | `8f67d6a` | ACE Step UI git ref |
| `VOICEBOX_REPO` | `https://github.com/jamiepine/voicebox` | Voicebox repository |
| `VOICEBOX_REF` | `v0.3.1` | Voicebox git ref |
| `WHISPER_REPO` | `https://github.com/jhj0517/Whisper-WebUI` | Whisper WebUI repository |
| `WHISPER_REF` | `v1.0.8` | Whisper WebUI git ref |
| `AI_TOOLKIT_REPO` | `https://github.com/ostris/ai-toolkit` | AI Toolkit repository |
| `AI_TOOLKIT_REF` | `4ad14d2` | AI Toolkit git ref |

## Licenses

This image ships the following vendor applications under their respective licenses:

| Application | License | Upstream |
|------------|---------|----------|
| ComfyUI | GPL-3.0 | [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI) |
| SD Forge (Classic) | AGPL-3.0 | [Haoming02/sd-webui-forge-classic](https://github.com/Haoming02/sd-webui-forge-classic) |
| Voicebox | MIT | [jamiepine/voicebox](https://github.com/jamiepine/voicebox) |
| Ostris AI Toolkit | MIT | [ostris/ai-toolkit](https://github.com/ostris/ai-toolkit) |
| Wan2GP | WanGP Community License 2.0 | [deepbeepmeep/Wan2GP](https://github.com/deepbeepmeep/Wan2GP) |
| Unsloth Studio | AGPL-3.0 | [unslothai/unsloth](https://github.com/unslothai/unsloth) |
| Whisper WebUI | Apache-2.0 | [jhj0517/Whisper-WebUI](https://github.com/jhj0517/Whisper-WebUI) |
| ACE-Step 1.5 | MIT | [ace-step/ACE-Step-1.5](https://github.com/ace-step/ACE-Step-1.5) |
| ACE-Step UI | MIT (per upstream README) | [fspecii/ace-step-ui](https://github.com/fspecii/ace-step-ui) |
| Selkies-GStreamer | MPL-2.0 | [selkies-project/selkies-gstreamer](https://github.com/selkies-project/selkies-gstreamer) |
| Blender | GPL-2.0-or-later | [blender.org](https://www.blender.org/) |

See `/LICENSES.md` in the image for license details and file locations.

## Useful Links

- [Blender](https://www.blender.org/) · [ComfyUI](https://github.com/Comfy-Org/ComfyUI) · [SD Forge](https://github.com/Haoming02/sd-webui-forge-classic) · [Wan2GP](https://github.com/deepbeepmeep/Wan2GP) · [ACE Step](https://github.com/ace-step/ACE-Step-1.5) · [Voicebox](https://github.com/jamiepine/voicebox) · [Whisper WebUI](https://github.com/jhj0517/Whisper-WebUI) · [AI Toolkit](https://github.com/ostris/ai-toolkit) · [Unsloth](https://github.com/unslothai/unsloth)
- [PyTorch Image Documentation](../../README.md)
- [Base Image Documentation](../../../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/aio-studio)
