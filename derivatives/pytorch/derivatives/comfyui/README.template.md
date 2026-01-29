# ComfyUI

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=ComfyUI)**

## What is this template?

This template gives you a **complete AI image and video generation environment** with ComfyUI running in a Docker container. ComfyUI provides a powerful node-based interface for creating complex AI workflows, from simple image generation to advanced multi-model pipelines.

**Think:** *"A visual programming environment for AI image and video generation - connect nodes to build any workflow you can imagine!"*

> **Latest builds:** Docker images are automatically rebuilt when new ComfyUI releases are detected. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Text-to-image generation** with Stable Diffusion, FLUX, and other models
- **Image-to-image transformations** for style transfer and editing
- **Inpainting and outpainting** to modify or extend existing images
- **ControlNet workflows** for precise composition control
- **LoRA and embedding support** for custom styles and concepts
- **Upscaling and enhancement** with ESRGAN and similar models
- **Text-to-video workflows** with models like Wan 2.x, Mochi, and LTX-Video
- **Image-to-video animation** from static images
- **Multi-model pipelines** combining different AI models
- **Batch processing** for generating multiple variations
- **Custom node development** with thousands of community nodes available
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Want a flexible, node-based interface for AI image generation
- Need to build complex multi-step AI workflows
- Want to experiment with different models and techniques
- Are creating AI art, concept art, or design assets
- Need batch processing capabilities for production workflows
- Want to use the latest models like FLUX, Wan 2.x, or Hunyuan
- Are building automated image/video generation pipelines
- Want access to thousands of community-created custom nodes

---

## Quick Start Guide

### **Step 1: Configure Your Setup**
Set your preferred configuration via environment variables:
- **`WORKSPACE`**: Custom workspace directory for your models and outputs
- **`PROVISIONING_SCRIPT`**: URL to auto-download models on first boot

> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=ComfyUI)"** when you've found a suitable GPU instance

### **Step 3: Wait for Setup**
ComfyUI will be ready automatically with ComfyUI-Manager pre-installed *(initial model downloads may take additional time)*

### **Step 4: Access Your Instance**
**Easy access:** Just click the **"Open"** button - authentication is handled automatically!

**For direct access:** If you want to connect from outside, you'll need:
- **ComfyUI URL:** Your instance IP with the mapped external port (e.g., `http://123.45.67.89:45678` or `https://` if enabled)
- **Auth Token:** Auto-generated when your instance starts - see the **"Finding Your Token"** section below

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings. If you don't enable HTTPS, we'll **try** to redirect you to a temporary secure Cloudflare link (though availability is occasionally limited).

### **Step 5: Start Creating**
Open the ComfyUI interface, load a workflow, and download models via ComfyUI-Manager. The default workflow is ready to use — just click **Queue Prompt** to generate your first image!

---

## Key Features

### **ComfyUI-Manager**
- One-click model downloads from CivitAI and Hugging Face
- Custom node installation from the community repository
- Workflow management and sharing tools
- Automatic dependency resolution for custom nodes

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Web Interface** | All ComfyUI operations | Click "Open" button |
| **SSH Terminal** | System administration | [SSH access](https://docs.vast.ai/instances/sshscp) |
| **SSH Tunnel** | Bypass authentication | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) to port 18188 |

### **Finding Your Token**
Your authentication token is available as `OPEN_BUTTON_TOKEN` in your instance environment. You can find it by:
- SSH: `echo $OPEN_BUTTON_TOKEN`
- Jupyter terminal: `echo $OPEN_BUTTON_TOKEN`

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| ComfyUI | 8188 | 18188 |
| Jupyter | 8080 | 8080 |

### **Instance Portal (Application Manager)**
- Web-based dashboard for managing your applications
- Cloudflare tunnels for easy sharing (no port forwarding needed!)
- Log monitoring for running services
- Start and stop services with a few clicks

### **Dynamic Provisioning**
Need specific software installed automatically? Set the `PROVISIONING_SCRIPT` environment variable to a plain-text script URL (GitHub, Gist, etc.), and we'll run your setup script on first boot!

### **Quick Package Installation**
For simpler setups, use these environment variables:
- `APT_PACKAGES`: Space-separated list of apt packages to install on first boot
- `PIP_PACKAGES`: Space-separated list of Python packages to install on first boot

### **Multiple Access Methods**
| Method | Best For | What You Get |
|--------|----------|--------------|
| **Web Interface** | Workflow creation and generation | ComfyUI node editor |
| **Jupyter** | Custom scripts and model management | Browser-based coding environment |
| **SSH** | File management and debugging | Full command-line access |
| **Instance Portal** | Managing services | Application manager dashboard |

### **Service Management**
- **Supervisor** manages all background services
- Easy commands: `supervisorctl status`, `supervisorctl restart comfyui`
- Add your own services with simple configuration files

### **Task Scheduling**
- **Cron** is enabled for automating routine tasks
- Schedule model downloads, cleanup scripts, or maintenance tasks
- Just add entries to your crontab to get started

### **Instance Control**
- **Vast.ai CLI** comes pre-installed with instance-specific API key
- Stop your instance from within itself: `vastai stop instance $CONTAINER_ID`
- Perfect for automated shutdown after batch processing completes

---

## Model Organization

ComfyUI organizes models in the following directories:
```
/workspace/ComfyUI/models/
├── checkpoints/     # Main model files (SD, FLUX, etc.)
├── loras/           # LoRA files
├── vae/             # VAE models
├── controlnet/      # ControlNet models
├── clip/            # CLIP models
├── embeddings/      # Textual embeddings
└── upscale_models/  # Upscaling models
```

**Note:** The Jupyter file browser cannot open the `checkpoints` folder directly due to a display limitation. Use the `ckpt` symlink instead, which points to the same directory.

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | ComfyUI workspace directory |
| `COMFYUI_ARGS` | `--disable-auto-launch --enable-cors-header --port 18188` | ComfyUI startup arguments |
| `APT_PACKAGES` | (none) | Space-separated apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated Python packages to install on first boot |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### **Recommended GPU Memory**
| Use Case | Minimum VRAM | Recommended VRAM |
|----------|--------------|------------------|
| SD 1.5 / SDXL | 8 GB | 12 GB |
| FLUX.1 Dev | 16 GB | 24 GB |
| Video Generation | 24 GB | 48 GB+ |

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `v0.8.2-cuda-12.9-py312`). This does not mean you need that exact CUDA version on the host.

**Minor version compatibility:** NVIDIA guarantees that an application built with any CUDA toolkit within a major version family will run on a driver from the same family. A `cuda-12.9` image runs on any CUDA 12.x driver (driver >= 525), and a `cuda-13.1` image runs on any CUDA 13.x driver (driver >= 580). The 12.x and 13.x families are separate.

**Forward compatibility:** All images include the [CUDA Compatibility Package](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html), which allows newer CUDA toolkit versions to run on older drivers. This is only available on **datacenter GPUs** (e.g., H100, A100, L40S, RTX Pro series) — for example, a `cuda-12.9` image can run on a datacenter host with a CUDA 12.1 driver. Consumer GPUs do not support forward compatibility and require a driver that natively supports the CUDA version.

---

## Customization Tips

### **Installing Software**
```bash
# You have root access - install anything!
apt update && apt install -y your-favorite-package

# Install Python packages
uv pip install --system requests openai anthropic

# Add system services
echo "your-service-config" > /etc/supervisor/conf.d/my-app.conf
supervisorctl reread && supervisorctl update
```

### **Template Customization**
Want to save your perfect setup? Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later.

---

## Need More Help?

- **ComfyUI Documentation:** [Official Repository](https://github.com/Comfy-Org/ComfyUI)
- **ComfyUI-Manager:** [Node and Model Manager](https://github.com/Comfy-Org/ComfyUI-Manager)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/comfyui)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
