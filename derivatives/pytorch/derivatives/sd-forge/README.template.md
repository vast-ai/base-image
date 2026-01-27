# Stable Diffusion WebUI Forge

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=SD%20Forge)**

## What is this template?

This template gives you a **complete AI image generation environment** with Stable Diffusion WebUI Forge running in a Docker container. Forge is built on top of the popular A1111 WebUI, optimized for faster inference and better resource management.

**Think:** *"The familiar A1111 interface, supercharged with better performance and native support for the latest models like FLUX and SD3!"*

### Supported Forge Variants

This template supports multiple Forge forks. The default image uses **Forge Neo**. Select your preferred variant by choosing the appropriate Docker image in the template editor:

| Variant | Description |
|---------|-------------|
| **[Forge Neo](https://github.com/Haoming02/sd-webui-forge-classic/tree/neo)** | Community fork with additional features and fixes **(default)** |
| **[Forge](https://github.com/lllyasviel/stable-diffusion-webui-forge)** | The original by lllyasviel |
| **[Forge Reforge](https://github.com/Panchovix/stable-diffusion-webui-reForge)** | Fork focused on extended model support |

> **Tip:** Click **"Edit"** on the template to change the Docker image and select a different Forge variant.

---

## What can I do with this?

### **Image Generation**
- **Text-to-image generation** with Stable Diffusion 1.5, SDXL, SD3, and FLUX models
- **Image-to-image transformations** for style transfer and editing
- **Inpainting and outpainting** to modify or extend existing images
- **ControlNet workflows** for precise composition control
- **LoRA and embedding support** for custom styles and concepts
- **Upscaling and enhancement** with built-in upscalers

### **Forge-Specific Features**
- **Optimized memory management** for running larger models
- **Native FLUX support** without additional configuration
- **Faster inference** through backend optimizations
- **SVD video generation** support built-in
- **Automatic memory optimization** based on available VRAM

### **Advanced Workflows**
- **Extension support** compatible with most A1111 extensions
- **Batch processing** for generating multiple variations
- **API integration** for automated workflows
- **Custom scripts** and processing pipelines

---

## Who is this for?

This is **perfect** if you:
- Want the familiar A1111 interface with better performance
- Need to run FLUX, SD3, or other large models efficiently
- Are migrating from A1111 and want compatibility with your existing workflows
- Want optimized memory management for limited VRAM
- Need a stable, well-tested image generation platform
- Are creating AI art, concept art, or design assets

---

## Quick Start Guide

### **Step 1: Configure Your Setup**
Set your preferred configuration via environment variables:
- **`WORKSPACE`**: Custom workspace directory for your models and outputs
- **`PROVISIONING_SCRIPT`**: URL to auto-download models on first boot

> **Template Customization:** Want to modify this setup? Click **edit**, make your changes, and save as your own template. Find it later in **"My Templates"**. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=SD%20Forge)"** when you've found a suitable GPU instance

### **Step 3: Wait for Setup**
Forge will be ready automatically *(initial model downloads may take additional time)*

### **Step 4: Access Your Environment**
**Easy access:** Click the **"Open"** button for instant access to the Forge interface!

**Direct access via mapped ports:**
- **Forge:** `http://your-instance-ip:7860` (main interface)

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in your Vast.ai account settings. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings.

### **Step 5: Start Creating**
Load a checkpoint, configure your settings, and start generating!

---

## Key Features

### **Authentication & Access**
| Method | Use Case | Access Point |
|--------|----------|--------------|
| **Web Interface** | All Forge operations | Click "Open" or port 7860 |
| **SSH Terminal** | System administration | [SSH access](https://docs.vast.ai/instances/sshscp) |
| **SSH Tunnel** | Bypass authentication | Forward local port to internal port 17860 |

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
| Forge | 7860 | 17860 |
| Jupyter | 8080 | 8080 |

### **Instance Portal (Application Manager)**
- **Service monitoring** with real-time status updates
- **Resource usage tracking** for GPU and memory
- **Log aggregation** for debugging issues
- **One-click service restarts** when needed

### **Advanced Features**

#### **Dynamic Provisioning**
Set `PROVISIONING_SCRIPT` environment variable to auto-download models and extensions from any public URL (GitHub, Gist, etc.)

#### **Multiple Access Methods**
| Method | Best For | Access Point |
|--------|----------|--------------|
| **Web Interface** | Image generation and configuration | Port 7860 |
| **Jupyter Notebook** | Custom scripts and model management | `/jupyter` endpoint |
| **SSH Terminal** | File management and debugging | SSH connection |
| **SSH Tunnel** | Auth-free local access | Forward to port 17860 |

#### **Service Management**
```bash
# Check service status
supervisorctl status

# Restart Forge
supervisorctl restart forge

# View service logs
supervisorctl tail -f forge
```

---

### **Model Organization**
Forge organizes models in the following directories:
```
/workspace/stable-diffusion-webui-forge/models/
├── Stable-diffusion/    # Main checkpoint files
├── Lora/                # LoRA files
├── VAE/                 # VAE models
├── ControlNet/          # ControlNet models
├── ESRGAN/              # Upscaling models
└── embeddings/          # Textual embeddings
```

### **Environment Variables Reference**
| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Forge workspace directory |
| `FORGE_ARGS` | `--port 17860` | Forge startup arguments |
| `PROVISIONING_SCRIPT` | (none) | Auto-setup script URL |
| `ENABLE_HTTPS` | `false` | Enable HTTPS connections (set in Vast.ai account settings) |

### **Recommended GPU Memory**
| Use Case | Minimum VRAM | Recommended VRAM |
|----------|--------------|------------------|
| SD 1.5 | 4 GB | 8 GB |
| SDXL | 8 GB | 12 GB |
| FLUX.1 Dev | 12 GB | 24 GB |
| SD3 Medium | 12 GB | 16 GB |

### **CUDA Forward Compatibility**
Images tagged `cu130` or above automatically enable CUDA forward compatibility. This allows them to run on datacenter GPUs (e.g., H100, A100, L40S, RTX Pro series) with older driver versions. Consumer GPUs (e.g., RTX 4090, RTX 5090) do not support forward compatibility and require a driver version that natively supports CUDA 13.0 or above.

---

## Need More Help?

### **Documentation & Resources**
- **Forge Documentation:** [Official Repository](https://github.com/lllyasviel/stable-diffusion-webui-forge)
- **A1111 Wiki:** [Features and Usage](https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki)
- **Base Image Features:** [GitHub Repository](https://github.com/vast-ai/base-image/)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)

### **Community & Support**
- **Forge GitHub:** [Issues & Discussions](https://github.com/lllyasviel/stable-diffusion-webui-forge/issues)
- **Vast.ai Support:** Use the messaging icon in the console

### **Getting Started Resources**
- **Model Downloads:** [CivitAI](https://civitai.com/) and [Hugging Face](https://huggingface.co/)
- **LoRA Training:** Community guides available on CivitAI
- **Extensions:** Compatible with most A1111 extensions

---

