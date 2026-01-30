# InvokeAI

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=InvokeAI)**

## What is this template?

This template gives you a **professional creative AI image generation environment** with InvokeAI running in a Docker container. InvokeAI provides an intuitive web interface for generating images with Stable Diffusion and FLUX models, complete with a powerful node-based workflow editor.

**Think:** *"Professional AI image generation made simple - create, inpaint, outpaint, and manage models with an elegant web UI!"*

> **Latest builds:** Docker images are automatically rebuilt when new InvokeAI releases are detected on GitHub. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Generate images** with Stable Diffusion and FLUX models
- **Inpaint and outpaint** to modify and extend existing images
- **Build complex workflows** with the node-based Canvas editor
- **Manage models** through the built-in Model Manager
- **Download models** from HuggingFace and Civitai directly in the UI
- **Create LoRA-enhanced images** with fine-tuned model support
- **Upscale images** with built-in upscaling models
- **Batch generate** with queue-based processing
- **Use ControlNet** for precise image guidance
- **IP Adapter support** for image-to-image style transfer
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Want a professional, polished UI for AI image generation
- Need inpainting, outpainting, and canvas editing tools
- Want to manage multiple Stable Diffusion and FLUX models easily
- Are an artist or designer using AI-assisted workflows
- Want a node-based editor for complex generation pipelines
- Need batch processing with queue management
- Want ControlNet and IP Adapter support out of the box
- Are building creative AI workflows for commercial or personal projects

---

## Quick Start Guide

### **Step 1: Configure Your Setup**
Set your preferred configuration via environment variables:
- **`WORKSPACE`**: Custom workspace directory for your models and outputs
- **`PROVISIONING_SCRIPT`**: URL to auto-download models on first boot

> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=InvokeAI)"** when you've found a suitable GPU instance

### **Step 3: Wait for Setup**
InvokeAI will be ready automatically with all required dependencies pre-installed *(initial setup may take additional time)*

### **Step 4: Access Your Instance**
**Easy access:** Just click the **"Open"** button - authentication is handled automatically!

**For direct access:** If you want to connect from outside, you'll need:
- **InvokeAI URL:** Your instance IP with the mapped external port (e.g., `http://123.45.67.89:9000` or `https://` if enabled)
- **Auth Token:** Auto-generated when your instance starts - see the **"Finding Your Token"** section below

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings. If you don't enable HTTPS, we'll **try** to redirect you to a temporary secure Cloudflare link (though availability is occasionally limited).

### **Step 5: Start Creating**
Open the InvokeAI interface, download models through the Model Manager, and start generating images!

---

## Key Features

### **Model Manager**
- Download models directly from HuggingFace and Civitai
- Manage Stable Diffusion 1.5, SDXL, and FLUX models
- LoRA, ControlNet, and IP Adapter model support
- Automatic model detection and configuration

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Web Interface** | All generation and editing | Click "Open" button |
| **SSH Terminal** | System administration | [SSH access](https://docs.vast.ai/instances/sshscp) |
| **SSH Tunnel** | Bypass authentication | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) to port 19000 |

### **Finding Your Token**
Your authentication token is available as `OPEN_BUTTON_TOKEN` in your instance environment. You can find it by:
- SSH: `echo $OPEN_BUTTON_TOKEN`
- Jupyter terminal: `echo $OPEN_BUTTON_TOKEN`

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| InvokeAI | 9000 | 19000 |
| Jupyter | 8080 | 8080 |

### **Instance Portal (Application Manager)**
- Web-based dashboard for managing your applications
- Cloudflare tunnels for easy sharing (no port forwarding needed!)
- Log monitoring for running services
- Start and stop services with a few clicks

### **Dynamic Provisioning**
Need models downloaded automatically? Set the `PROVISIONING_SCRIPT` environment variable to a plain-text script URL (GitHub, Gist, etc.), and we'll run your setup script on first boot!

### **Multiple Access Methods**
| Method | Best For | What You Get |
|--------|----------|--------------|
| **Web Interface** | Image generation and editing | InvokeAI web UI |
| **Jupyter** | Custom scripts and model management | Browser-based coding environment |
| **SSH** | File management and debugging | Full command-line access |
| **Instance Portal** | Managing services | Application manager dashboard |

### **Service Management**
- **Supervisor** manages all background services
- Easy commands: `supervisorctl status`, `supervisorctl restart invokeai`
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

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Workspace directory for models and outputs |
| `INVOKEAI_ARGS` | `--port 19000` | Startup arguments passed to InvokeAI |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### **Recommended GPU Memory**
| Use Case | Minimum VRAM | Recommended VRAM |
|----------|--------------|------------------|
| SD 1.5 generation | 4 GB | 8 GB |
| SDXL generation | 8 GB | 12 GB |
| FLUX generation | 12 GB | 24 GB |
| ControlNet + generation | 8 GB | 16 GB |

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `5.8.1-cuda-12.9-py312`). This does not mean you need that exact CUDA version on the host.

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

- **InvokeAI Documentation:** [Official Docs](https://invoke-ai.github.io/InvokeAI/)
- **InvokeAI GitHub:** [Repository](https://github.com/invoke-ai/InvokeAI)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/invokeai)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
