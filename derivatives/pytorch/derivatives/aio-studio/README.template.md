# AIO Studio

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=AIO+Studio)**

## What is this template?

This template gives you a **complete creative AI studio** with eight powerful applications in a single container. Generate images, create videos, make music, synthesize voices, transcribe audio, train LoRAs, and fine-tune LLMs — all from one instance.

**Think:** *"One GPU instance, eight creative AI tools — start what you need, stop what you don't."*

> **Important:** This image has many open ports. We **strongly recommend** setting `ENABLE_HTTPS=true` in your environment variables, as Cloudflare tunnels may not be available for all ports. See the HTTPS section below.

---

## What's included?

| Application | What it does | Start command |
|------------|-------------|---------------|
| **ComfyUI** | Node-based image & video generation | `supervisorctl start comfyui` |
| **SD Forge** | Stable Diffusion WebUI (classic) | `supervisorctl start forge` |
| **Wan2GP** | Video generation (Wan 2.x models) | `supervisorctl start wan2gp` |
| **ACE Step 1.5** | AI music generation | `supervisorctl start ace-step` |
| **Voicebox** | Text-to-speech synthesis | `supervisorctl start voicebox` |
| **Whisper WebUI** | Speech-to-text transcription | `supervisorctl start whisper-webui` |
| **Ostris AI Toolkit** | LoRA and model training | `supervisorctl start ai-toolkit` |
| **Unsloth Studio** | LLM fine-tuning & serving | `supervisorctl start unsloth-studio` |

**No applications auto-start.** You choose what to run via the **Supervisor** tab in Instance Portal or the terminal. This keeps VRAM free for the tools you actually need.

---

## Who is this for?

This is **perfect** if you:
- Want multiple creative AI tools without managing separate instances
- Need to switch between image generation, video, music, voice, and training workflows
- Are exploring different tools and want everything in one place
- Want to use ComfyUI and SD Forge with a shared model library
- Need dedicated video generation with Wan2GP alongside image workflows
- Want speech-to-text transcription alongside text-to-speech synthesis
- Need to train LoRAs with AI Toolkit and immediately test them in ComfyUI or Forge
- Want to fine-tune LLMs and serve them immediately via an OpenAI-compatible API
- Are building multi-modal creative pipelines

---

## Quick Start Guide

### **Step 1: Configure Your Setup**
Set your preferred configuration via environment variables:
- **`ENABLE_HTTPS`**: Set to `true` (strongly recommended — see HTTPS section below)
- **`WORKSPACE`**: Custom workspace directory for your models and outputs
- **`PROVISIONING_SCRIPT`**: URL to auto-download models on first boot

> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=AIO+Studio)"** when you've found a suitable GPU instance

### **Step 3: Wait for Setup**
The container will start with Instance Portal and Jupyter ready. Applications are installed but not running yet.

### **Step 4: Start Your Applications**
Open Instance Portal and go to the **Supervisor** tab. Click **Start** next to the applications you want to use. Or use the terminal:
```bash
# Example: start ComfyUI and Voicebox
supervisorctl start comfyui voicebox
```

### **Step 5: Access Your Applications**
Click the application tabs in Instance Portal to open each tool in your browser. Each application has its own tab.

---

## HTTPS Access (Strongly Recommended)

This image exposes **many application ports**. Cloudflare tunnels may not be available for all of them, so we **strongly recommend** enabling HTTPS:

1. Set `ENABLE_HTTPS=true` in the **Environment Variables** section of your Vast.ai account settings or template configuration
2. [Install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings
3. All applications will be accessible over secure HTTPS connections

Without HTTPS enabled, some applications may only be accessible via direct HTTP to the instance IP, which is less secure.

---

## Key Features

### **Eight Applications, One Instance**

| Application | Start Command | Port |
|------------|---------------|------|
| ComfyUI | `supervisorctl start comfyui` | 8188 |
| SD Forge | `supervisorctl start forge` | 7860 |
| Wan2GP | `supervisorctl start wan2gp` | 7861 |
| ACE Step | `supervisorctl start ace-step` | 13000 |
| Voicebox | `supervisorctl start voicebox` | 7493 |
| Whisper WebUI | `supervisorctl start whisper-webui` | 17862 |
| AI Toolkit | `supervisorctl start ai-toolkit` | 18675 |
| Unsloth Studio | `supervisorctl start unsloth-studio` | 8888 |

### **Shared Model Library**
ComfyUI and SD Forge share the same model directories. Download a checkpoint in one tool, use it in both:
```
/workspace/ComfyUI/models/
├── checkpoints/     # Shared with Forge (models/Stable-diffusion)
├── loras/           # Shared with Forge (models/Lora)
├── vae/             # Shared with Forge (models/VAE)
├── controlnet/      # Shared with Forge (models/ControlNet)
├── embeddings/      # Shared with Forge (embeddings)
└── upscale_models/  # Shared with Forge (models/ESRGAN)
```

### **Unsloth Studio: Train and Serve**
Unsloth Studio isn't just for fine-tuning — it can also **serve your models** directly. Use the **Chat tab** in Studio to interact with any loaded model, or connect external tools via the built-in OpenAI-compatible API:
- **Chat UI:** Built into the Studio web interface
- **API endpoint:** `POST http://<host>:8888/v1/chat/completions`
- **Model listing:** `GET http://<host>:8888/v1/models`

Fine-tune a model, then immediately chat with it or connect tools like Open WebUI or SillyTavern.

### **On-Demand Services**
No applications auto-start, so your VRAM stays free until you need it:
- **Instance Portal:** Use the **Supervisor** tab — click Start/Stop next to each service
- **Terminal:** `supervisorctl start comfyui` / `supervisorctl stop comfyui`
- **Multiple at once:** `supervisorctl start comfyui ace-step voicebox`

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Instance Portal** | Manage and access all apps | Click "Open" button |
| **SSH Terminal** | System administration | [SSH access](https://docs.vast.ai/instances/sshscp) |
| **SSH Tunnel** | Bypass authentication | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) |

### **Finding Your Token**
Your authentication token is available as `OPEN_BUTTON_TOKEN` in your instance environment. You can find it by:
- SSH: `echo $OPEN_BUTTON_TOKEN`
- Jupyter terminal: `echo $OPEN_BUTTON_TOKEN`

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| ComfyUI | 8188 | 18188 |
| SD Forge | 7860 | 17860 |
| Wan2GP | 7861 | 7861 |
| AI Toolkit | 18675 | 8675 |
| ACE Step | 13000 | 3000 |
| Unsloth Studio | 8888 | 18888 |
| Voicebox | 7493 | 17493 |
| Whisper WebUI | 17862 | 7862 |
| Jupyter | 8080 | 8080 |

### **Dynamic Provisioning**
Need specific models or software installed automatically? Set the `PROVISIONING_SCRIPT` environment variable to a plain-text script URL (GitHub, Gist, etc.), and we'll run your setup script on first boot!

### **Instance Control**
- **Vast.ai CLI** comes pre-installed with instance-specific API key
- Stop your instance from within itself: `vastai stop instance $CONTAINER_ID`
- Perfect for automated shutdown after batch processing completes

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_HTTPS` | (none) | Set to `true` for secure HTTPS access (strongly recommended) |
| `WORKSPACE` | `/workspace` | Workspace directory for models and outputs |
| `COMFYUI_ARGS` | `--disable-auto-launch --enable-cors-header --port 18188` | ComfyUI startup arguments |
| `FORGE_ARGS` | `--port 17860` | SD Forge startup arguments |
| `VOICEBOX_ARGS` | `--host 127.0.0.1 --port 17493` | Voicebox startup arguments |
| `UNSLOTH_STUDIO_ARGS` | `--host 127.0.0.1 --port 18888` | Unsloth Studio startup arguments |
| `AI_TOOLKIT_START_CMD` | `npm run start` | AI Toolkit start command |
| `ACESTEP_LM_MODEL_PATH` | `acestep-5Hz-lm-4B` | ACE Step language model path |
| `WAN2GP_PORT` | `7861` | Wan2GP server port |
| `WHISPER_UI_ARGS` | `--whisper_type whisper --server_port 7862` | Whisper WebUI startup arguments |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### **Recommended GPU Memory**
| Use Case | Minimum VRAM | Recommended VRAM |
|----------|--------------|------------------|
| Image generation (SD 1.5 / SDXL) | 8 GB | 12 GB |
| Image generation (FLUX) | 16 GB | 24 GB |
| Video generation (Wan2GP) | 12 GB | 24 GB |
| Music generation (ACE Step) | 8 GB | 16 GB |
| Voice synthesis (Voicebox) | 8 GB | 12 GB |
| Speech transcription (Whisper) | 4 GB | 8 GB |
| LoRA training (AI Toolkit) | 16 GB | 24 GB |
| LLM fine-tuning (Unsloth) | 16 GB | 48 GB+ |
| Multiple apps simultaneously | 24 GB | 48 GB+ |

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against. This does not mean you need that exact CUDA version on the host.

**Minor version compatibility:** NVIDIA guarantees that an application built with any CUDA toolkit within a major version family will run on a driver from the same family. A `cuda-12.9` image runs on any CUDA 12.x driver (driver >= 525), and a `cuda-13.1` image runs on any CUDA 13.x driver (driver >= 580). The 12.x and 13.x families are separate.

**Forward compatibility:** All images include the [CUDA Compatibility Package](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html), which allows newer CUDA toolkit versions to run on older drivers. This is only available on **datacenter GPUs** (e.g., H100, A100, L40S, RTX Pro series) — for example, a `cuda-12.9` image can run on a datacenter host with a CUDA 12.1 driver. Consumer GPUs do not support forward compatibility and require a driver that natively supports the CUDA version.

---

## Customization Tips

### **Installing Software**
```bash
# You have root access - install anything!
apt update && apt install -y your-favorite-package

# Install Python packages into the main venv (torch 2.10)
. /venv/main/bin/activate
uv pip install requests openai anthropic

# Install into the Voicebox/Whisper venv (torch 2.9)
. /venv/torch-2.9.1/bin/activate
uv pip install your-audio-package

# Install into the AI Toolkit/Wan2GP venv (torch 2.7)
. /venv/torch-2.7.1/bin/activate
uv pip install your-training-package
```

### **Adding Custom Supervisor Services**
```bash
echo "[program:my-app]
command=/path/to/my-app
autostart=false
autorestart=true" > /etc/supervisor/conf.d/my-app.conf
supervisorctl reread && supervisorctl update
```

### **Template Customization**
Want to save your perfect setup? Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later.

---

## Need More Help?

- **ComfyUI:** [Official Repository](https://github.com/Comfy-Org/ComfyUI) · [ComfyUI-Manager](https://github.com/Comfy-Org/ComfyUI-Manager)
- **SD Forge:** [Official Repository](https://github.com/Haoming02/sd-webui-forge-classic)
- **Wan2GP:** [Official Repository](https://github.com/deepbeepmeep/Wan2GP)
- **ACE Step:** [Official Repository](https://github.com/ace-step/ACE-Step-1.5)
- **Voicebox:** [Official Repository](https://github.com/jamiepine/voicebox)
- **Whisper WebUI:** [Official Repository](https://github.com/jhj0517/Whisper-WebUI)
- **AI Toolkit:** [Official Repository](https://github.com/ostris/ai-toolkit)
- **Unsloth:** [Official Repository](https://github.com/unslothai/unsloth)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/aio-studio)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
