# ComfyUI

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=ComfyUI)**

## What is this template?

This template gives you a **complete AI image and video generation environment** with ComfyUI running in a Docker container. ComfyUI provides a powerful node-based interface for creating complex AI workflows, from simple image generation to advanced multi-model pipelines.

**Think:** *"A visual programming environment for AI image and video generation - connect nodes to build any workflow you can imagine!"*

---

## What can I do with this?

### **Image Generation**
- **Text-to-image generation** with Stable Diffusion, FLUX, and other models
- **Image-to-image transformations** for style transfer and editing
- **Inpainting and outpainting** to modify or extend existing images
- **ControlNet workflows** for precise composition control
- **LoRA and embedding support** for custom styles and concepts
- **Upscaling and enhancement** with ESRGAN and similar models

### **Video Generation**
- **Text-to-video workflows** with models like Wan 2.x, Mochi, and LTX-Video
- **Image-to-video animation** from static images
- **Video-to-video transformations** for style transfer
- **Frame interpolation** for smooth animations

### **Advanced Workflows**
- **Multi-model pipelines** combining different AI models
- **Batch processing** for generating multiple variations
- **Custom node development** for specialized functionality
- **API integration** for automated workflows
- **Workflow sharing** with the ComfyUI community

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

> **Template Customization:** Want to modify this setup? Click **edit**, make your changes, and save as your own template. Find it later in **"My Templates"**. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=ComfyUI)"** when you've found a suitable GPU instance

### **Step 3: Wait for Setup**
ComfyUI will be ready automatically with ComfyUI-Manager pre-installed *(initial model downloads may take additional time)*

### **Step 4: Access Your Environment**
**Easy access:** Click the **"Open"** button for instant access to the ComfyUI interface!

**Direct access via mapped ports:**
- **ComfyUI:** `http://your-instance-ip:8188` (main interface)

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in your Vast.ai account settings. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings.

### **Step 5: Start Creating**
Load a workflow, download models via ComfyUI-Manager, and start generating!

---

## Key Features

### **ComfyUI-Manager**
- **One-click model downloads** from CivitAI and Hugging Face
- **Custom node installation** from the community repository
- **Workflow management** and sharing tools
- **Automatic dependency resolution** for custom nodes

### **Authentication & Access**
| Method | Use Case | Access Point |
|--------|----------|--------------|
| **Web Interface** | All ComfyUI operations | Click "Open" or port 8188 |
| **SSH Terminal** | System administration | [SSH access](https://docs.vast.ai/instances/sshscp) |
| **SSH Tunnel** | Bypass authentication | Forward local port to internal port 18188 |

### **Finding Your Credentials**
Access your authentication details:
```bash
# SSH into your instance
echo $OPEN_BUTTON_TOKEN          # For web access
```

*Use `https://` instead of `http://` if you've enabled HTTPS in your account settings.*

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| ComfyUI | 8188 | 18188 |
| Jupyter | 8080 | 8080 |

### **Instance Portal (Application Manager)**
- **Service monitoring** with real-time status updates
- **Resource usage tracking** for GPU and memory
- **Log aggregation** for debugging issues
- **One-click service restarts** when needed

### **Advanced Features**

#### **Dynamic Provisioning**
Set `PROVISIONING_SCRIPT` environment variable to auto-download models and custom nodes from any public URL (GitHub, Gist, etc.)

#### **Multiple Access Methods**
| Method | Best For | Access Point |
|--------|----------|--------------|
| **Web Interface** | Workflow creation and generation | Port 8188 |
| **Jupyter Notebook** | Custom scripts and model management | `/jupyter` endpoint |
| **SSH Terminal** | File management and debugging | SSH connection |
| **SSH Tunnel** | Auth-free local access | Forward to port 18188 |

#### **Service Management**
```bash
# Check service status
supervisorctl status

# Restart ComfyUI
supervisorctl restart comfyui

# View service logs
supervisorctl tail -f comfyui
```

---

### **Model Organization**
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

### **Environment Variables Reference**
| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | ComfyUI workspace directory |
| `COMFYUI_ARGS` | `--disable-auto-launch --enable-cors-header --port 18188` | ComfyUI startup arguments |
| `PROVISIONING_SCRIPT` | (none) | Auto-setup script URL |
| `ENABLE_HTTPS` | `false` | Enable HTTPS connections (set in Vast.ai account settings) |

### **Recommended GPU Memory**
| Use Case | Minimum VRAM | Recommended VRAM |
|----------|--------------|------------------|
| SD 1.5 / SDXL | 8 GB | 12 GB |
| FLUX.1 Dev | 16 GB | 24 GB |
| Video Generation | 24 GB | 48 GB+ |

### **CUDA Forward Compatibility**
Images tagged `cu130` or above automatically enable CUDA forward compatibility. This allows them to run on datacenter GPUs (e.g., H100, A100, L40S, RTX Pro series) with older driver versions. Consumer GPUs (e.g., RTX 4090, RTX 5090) do not support forward compatibility and require a driver version that natively supports CUDA 13.0 or above.

---

## Need More Help?

### **Documentation & Resources**
- **ComfyUI Documentation:** [Official Repository](https://github.com/Comfy-Org/ComfyUI)
- **ComfyUI-Manager:** [Node and Model Manager](https://github.com/Comfy-Org/ComfyUI-Manager)
- **Base Image Features:** [GitHub Repository](https://github.com/vast-ai/base-image/)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)

### **Community & Support**
- **ComfyUI GitHub:** [Issues & Discussions](https://github.com/Comfy-Org/ComfyUI)
- **Vast.ai Support:** Use the messaging icon in the console

### **Getting Started Resources**
- **Example Workflows:** Pre-built workflows included in the image
- **ComfyUI Examples:** [Community workflow gallery](https://comfyworkflows.com/)
- **Custom Nodes:** Thousands available via ComfyUI-Manager

---

