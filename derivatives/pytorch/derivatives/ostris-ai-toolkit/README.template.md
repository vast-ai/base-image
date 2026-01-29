# Ostris AI Toolkit

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Ostris%20AI%20Toolkit)**

## What is this template?

This template gives you a **complete AI model training environment** with the Ostris AI Toolkit running in a Docker container. The toolkit provides an intuitive web interface for training custom FLUX, Stable Diffusion, and video models like Wan 2.x with your own datasets.

**Think:** *"Professional AI training made simple - train custom FLUX, Stable Diffusion, and video models with your own data!"*

> **Latest builds:** Docker images are automatically rebuilt when new AI Toolkit releases are detected. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Train FLUX LoRAs** for the latest generation of image models
- **Train Stable Diffusion LoRAs** using your own images and concepts
- **Train video models** like Wan 2.x for custom video generation
- **Create character LoRAs** from portrait datasets
- **Train style LoRAs** from artistic collections
- **Build concept models** for specific objects or scenes
- **Upload and organize training datasets** through the web interface
- **Monitor training progress** with real-time visualization
- **Resume interrupted training** sessions automatically
- **Export trained models** in multiple formats
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Want to train custom FLUX or Stable Diffusion models without complex setup
- Need to train LoRAs for specific characters, styles, or concepts
- Want to train video generation models like Wan 2.x
- Are an artist or designer wanting personalized AI image/video models
- Want to experiment with different training techniques and parameters
- Need a simple interface for managing training datasets
- Are building custom AI image or video generation solutions
- Want to fine-tune models for commercial or personal projects

---

## Quick Start Guide

### **Step 1: Configure Your Setup**
Set your preferred configuration via environment variables:
- **`WORKSPACE`**: Custom workspace directory for your datasets and models
- **`PROVISIONING_SCRIPT`**: URL to auto-download dependencies on first boot

> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Ostris%20AI%20Toolkit)"** when you've found a suitable GPU instance

### **Step 3: Wait for Setup**
The Ostris AI Toolkit will be ready automatically with all required dependencies pre-installed *(initial setup may take additional time)*

### **Step 4: Access Your Instance**
**Easy access:** Just click the **"Open"** button - authentication is handled automatically!

**For direct access:** If you want to connect from outside, you'll need:
- **AI Toolkit URL:** Your instance IP with the mapped external port (e.g., `http://123.45.67.89:18675` or `https://` if enabled)
- **Auth Token:** Auto-generated when your instance starts - see the **"Finding Your Token"** section below

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings. If you don't enable HTTPS, we'll **try** to redirect you to a temporary secure Cloudflare link (though availability is occasionally limited).

### **Step 5: Start Training**
Open the AI Toolkit interface, upload your training images, configure your parameters, and start creating custom models!

---

## Key Features

### **Dataset Management**
- Batch image upload with drag-and-drop interface
- Automatic image validation and format conversion
- Caption editing with bulk operations
- Dataset preview and organization tools

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Web Interface** | All training operations | Click "Open" button |
| **SSH Terminal** | System administration | [SSH access](https://docs.vast.ai/instances/sshscp) |
| **SSH Tunnel** | Bypass authentication | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) to port 8675 |

### **Finding Your Token**
Your authentication token is available as `OPEN_BUTTON_TOKEN` in your instance environment. You can find it by:
- SSH: `echo $OPEN_BUTTON_TOKEN`
- Jupyter terminal: `echo $OPEN_BUTTON_TOKEN`

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| AI Toolkit UI | 18675 | 8675 |
| Jupyter | 8080 | 8080 |

### **Instance Portal (Application Manager)**
- Web-based dashboard for managing your applications
- Cloudflare tunnels for easy sharing (no port forwarding needed!)
- Log monitoring for running services
- Start and stop services with a few clicks

### **Dynamic Provisioning**
Need specific software installed automatically? Set the `PROVISIONING_SCRIPT` environment variable to a plain-text script URL (GitHub, Gist, etc.), and we'll run your setup script on first boot!

### **Multiple Access Methods**
| Method | Best For | What You Get |
|--------|----------|--------------|
| **Web Interface** | Training and dataset management | AI Toolkit web UI |
| **Jupyter** | Custom scripts and model management | Browser-based coding environment |
| **SSH** | File management and debugging | Full command-line access |
| **Instance Portal** | Managing services | Application manager dashboard |

### **Service Management**
- **Supervisor** manages all background services
- Easy commands: `supervisorctl status`, `supervisorctl restart ai-toolkit`
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

## Dataset Preparation Tips

For best training results:
- **Image Quality:** Use high-resolution, clear images (512px minimum)
- **Dataset Size:** 20-100 images for LoRA, 5-20 for Dreambooth
- **Consistency:** Similar lighting and composition when possible
- **Captions:** Accurate, descriptive text for each image
- **Variety:** Different angles and poses for character training

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Training workspace directory |
| `AI_TOOLKIT_START_CMD` | `npm run start` | Command to start the AI Toolkit UI |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### **Recommended GPU Memory**
| Use Case | Minimum VRAM | Recommended VRAM |
|----------|--------------|------------------|
| LoRA Training (SD 1.5 / SDXL) | 8 GB | 12 GB |
| LoRA Training (FLUX) | 16 GB | 24 GB |
| Video Model Training | 24 GB | 48 GB+ |

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `ea912d2-cuda-12.9`). This does not mean you need that exact CUDA version on the host.

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

- **Ostris AI Toolkit Documentation:** [Official Repository](https://github.com/ostris/ai-toolkit)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/ostris-ai-toolkit)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
