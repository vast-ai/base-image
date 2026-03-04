# ACE-Step 1.5

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=ACE-Step+1.5)**

## What is this template?

This template gives you a **complete AI music generation environment** with ACE-Step 1.5 running on a Vast.ai instance. ACE-Step provides a state-of-the-art music generation model with both an API server and a dedicated web UI for creating music from text prompts.

**Think:** *"AI music generation made easy - describe a song and let ACE-Step compose it for you!"*

> **Pre-built image:** This template uses a pre-built Docker image with ACE-Step 1.5 and its web UI already installed. Both services start automatically when the instance boots — no provisioning delay.

---

## What can I do with this?

- **Generate music from text** describing genre, mood, instruments, and style
- **Control song structure** with lyrics and timing markers
- **Use the web UI** for an interactive music creation experience
- **Access the REST API** for programmatic music generation and integration
- **Experiment with models** including the default 4B parameter language model
- **Generate in various genres** from classical to electronic, hip-hop to jazz
- **Customize generation parameters** like duration, temperature, and guidance scale
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Want to generate music using AI from text descriptions
- Need an API for integrating AI music generation into your workflow
- Are a musician looking for AI-assisted composition tools
- Want to experiment with state-of-the-art music generation models
- Need to generate background music, soundtracks, or audio content
- Are a developer building applications that require music generation
- Want a simple web interface for quick music creation

---

## Quick Start Guide

### **Step 1: Configure Your Setup**
Set your preferred configuration via environment variables:
- **`WORKSPACE`**: Custom workspace directory (default: `/workspace`)
- **`ACESTEP_LM_MODEL_PATH`**: Language model to use (default: `acestep-5Hz-lm-4B`)

> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=ACE-Step+1.5)"** when you've found a suitable GPU instance

### **Step 3: Wait for Startup**
The API server starts first, followed by the UI once the API is ready. Models are downloaded on first generation.

### **Step 4: Access Your Instance**
**Easy access:** Just click the **"Open"** button - authentication is handled automatically!

**For direct access:** If you want to connect from outside, you'll need:
- **ACE-Step UI URL:** Your instance IP with the mapped external port for the UI (e.g., `http://123.45.67.89:13000`)
- **ACE-Step API URL:** Your instance IP with the mapped external port for the API (e.g., `http://123.45.67.89:18001`)
- **Auth Token:** Auto-generated when your instance starts - see the **"Finding Your Token"** section below

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings. If you don't enable HTTPS, we'll **try** to redirect you to a temporary secure Cloudflare link (though availability is occasionally limited).

### **Step 5: Start Creating**
Open the ACE-Step UI, enter a text description of the music you want to generate, and hit generate! You can also interact with the API directly at `/docs` for programmatic access.

---

## Key Features

### **ACE-Step 1.5 API**
- REST API server for music generation
- Interactive API documentation at `/docs`
- Programmatic control over all generation parameters
- Supports batch generation and custom configurations

### **ACE-Step Web UI**
- Intuitive web interface for music generation
- Real-time generation status and audio playback
- Parameter controls for fine-tuning output
- Built on a modern Node.js stack

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Web UI** | Interactive music generation | Click "Open" button |
| **API** | Programmatic music generation | Use external API port |
| **SSH Terminal** | System administration | [SSH access](https://docs.vast.ai/instances/sshscp) |
| **SSH Tunnel** | Bypass authentication | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) to ports 8001/3000 |

### **Finding Your Token**
Your authentication token is available as `OPEN_BUTTON_TOKEN` in your instance environment. You can find it by:
- SSH: `echo $OPEN_BUTTON_TOKEN`
- Jupyter terminal: `echo $OPEN_BUTTON_TOKEN`

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| ACE-Step API | 18001 | 8001 |
| ACE-Step UI | 13000 | 3000 |
| Jupyter | 8080 | 8080 |

### **Instance Portal (Application Manager)**
- Web-based dashboard for managing your applications
- Cloudflare tunnels for easy sharing (no port forwarding needed!)
- Log monitoring for running services
- Start and stop services with a few clicks

### **Multiple Access Methods**
| Method | Best For | What You Get |
|--------|----------|--------------|
| **Web UI** | Interactive music generation | ACE-Step web interface |
| **API** | Integration and automation | REST API with docs |
| **Jupyter** | Custom scripts and experimentation | Browser-based coding environment |
| **SSH** | File management and debugging | Full command-line access |
| **Instance Portal** | Managing services | Application manager dashboard |

### **Service Management**
- **Supervisor** manages all background services
- Easy commands: `supervisorctl status`, `supervisorctl restart ace-step-api`, `supervisorctl restart ace-step-ui`
- Add your own services with simple configuration files

### **Task Scheduling**
- **Cron** is enabled for automating routine tasks
- Schedule cleanup scripts or maintenance tasks
- Just add entries to your crontab to get started

### **Instance Control**
- **Vast.ai CLI** comes pre-installed with instance-specific API key
- Stop your instance from within itself: `vastai stop instance $CONTAINER_ID`
- Perfect for automated shutdown after batch processing completes

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Workspace directory for ACE-Step and outputs |
| `ACESTEP_LM_MODEL_PATH` | `acestep-5Hz-lm-4B` | Language model path for generation |
| `PROVISIONING_SCRIPT` | (none) | URL to an optional setup script to run on first boot |

### **Recommended GPU Memory**
| Use Case | Minimum VRAM | Recommended VRAM |
|----------|--------------|------------------|
| Music generation (4B model) | 12 GB | 16 GB |

---

## CUDA Compatibility

This template uses a pre-built image tagged with the CUDA version it was built against. This does not mean you need that exact CUDA version on the host.

**Minor version compatibility:** NVIDIA guarantees that an application built with any CUDA toolkit within a major version family will run on a driver from the same family. A `cuda-12.9` image runs on any CUDA 12.x driver (driver >= 525), and a `cuda-13.1` image runs on any CUDA 13.x driver (driver >= 580). The 12.x and 13.x families are separate.

**Forward compatibility:** All images include the [CUDA Compatibility Package](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html), which allows newer CUDA toolkit versions to run on older drivers. This is only available on **datacenter GPUs** (e.g., H100, A100, L40S, RTX Pro series) — for example, a `cuda-12.9` image can run on a datacenter host with a CUDA 12.1 driver. Consumer GPUs do not support forward compatibility and require a driver that natively supports the CUDA version.

---

## Customization Tips

### **Installing Software**
```bash
# You have root access - install anything!
apt update && apt install -y your-favorite-package

# Install Python packages into the shared venv
uv pip install requests openai anthropic

# Add system services
echo "your-service-config" > /etc/supervisor/conf.d/my-app.conf
supervisorctl reread && supervisorctl update
```

### **Template Customization**
Want to save your perfect setup? Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later.

---

## Need More Help?

- **ACE-Step 1.5:** [GitHub Repository](https://github.com/ace-step/ACE-Step-1.5)
- **ACE-Step UI:** [GitHub Repository](https://github.com/fspecii/ace-step-ui)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
