# Unsloth Studio

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Unsloth+Studio)**

## What is this template?

This template gives you a **complete LLM fine-tuning environment** with Unsloth Studio running in a Docker container. Unsloth Studio provides a web-based interface for training, fine-tuning, and running open-source language models with optimized performance.

**Think:** *"A web UI for fine-tuning LLMs like Qwen, DeepSeek, Llama, and Gemma - up to 2x faster with 70% less memory than standard methods!"*

---

## What can I do with this?

- **Fine-tune LLMs** with LoRA and QLoRA for efficient training on consumer GPUs
- **Train on custom datasets** with built-in dataset preparation tools
- **Run inference** on fine-tuned and base models via the built-in GGUF server
- **Export models** to GGUF, GGML, and other formats for deployment
- **Data recipe designer** for creating and curating training datasets
- **Monitor training** with real-time loss curves and GPU utilization
- **Manage models** from Hugging Face Hub directly in the UI
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Want to fine-tune open-source LLMs without writing code
- Need an efficient training setup that maximizes your GPU budget
- Are creating custom AI assistants or domain-specific models
- Want to experiment with different models, datasets, and training strategies
- Need to export fine-tuned models for local inference (llama.cpp, Ollama, etc.)
- Are a researcher exploring parameter-efficient fine-tuning techniques

---

## Quick Start Guide

### **Step 1: Configure Your Setup**
Set your preferred configuration via environment variables:
- **`UNSLOTH_STUDIO_ARGS`**: Custom startup arguments for Unsloth Studio
- **`PROVISIONING_SCRIPT`**: URL to auto-download models on first boot

> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Unsloth+Studio)"** when you've found a suitable GPU instance

### **Step 3: Wait for Setup**
Unsloth Studio will be ready automatically with llama.cpp pre-built for GGUF inference

### **Step 4: Access Your Instance**
**Easy access:** Just click the **"Open"** button - authentication is handled automatically!

**For direct access:** If you want to connect from outside, you'll need:
- **Unsloth Studio URL:** Your instance IP with the mapped external port (e.g., `http://123.45.67.89:45678` or `https://` if enabled)
- **Auth Token:** Auto-generated when your instance starts - see the **"Finding Your Token"** section below

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings. If you don't enable HTTPS, we'll **try** to redirect you to a temporary secure Cloudflare link (though availability is occasionally limited).

### **Step 5: Start Training**
Open the Unsloth Studio interface, select a base model, upload your dataset, configure training parameters, and click **Start Training**. Your first fine-tune can be running in minutes!

---

## Key Features

### **Training**
- LoRA and QLoRA fine-tuning with up to 2x speedup over standard methods
- Support for Llama, Qwen, DeepSeek, Gemma, Mistral, Phi, and more
- Real-time training progress with loss curves and GPU monitoring
- Configurable hyperparameters (learning rate, epochs, batch size, etc.)

### **Data Recipes**
- Built-in dataset designer for creating training data
- Import datasets from Hugging Face Hub
- LLM-assisted data generation and quality scoring
- Support for instruction, chat, and text completion formats

### **Inference & Export**
- Built-in GGUF inference via llama.cpp (pre-compiled with CUDA)
- Export trained models to GGUF for use with llama.cpp, Ollama, and more
- Quantization options (Q4, Q5, Q8, etc.) for efficient deployment

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Web Interface** | All Studio operations | Click "Open" button |
| **SSH Terminal** | System administration | [SSH access](https://docs.vast.ai/instances/sshscp) |
| **SSH Tunnel** | Bypass authentication | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) to port 18888 |

### **Finding Your Token**
Your authentication token is available as `OPEN_BUTTON_TOKEN` in your instance environment. You can find it by:
- SSH: `echo $OPEN_BUTTON_TOKEN`
- Jupyter terminal: `echo $OPEN_BUTTON_TOKEN`

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Unsloth Studio | 8888 | 18888 |
| Jupyter | 8080 | 18080 |

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
| **Web Interface** | Training and inference | Unsloth Studio UI |
| **Jupyter** | Custom scripts and model management | Browser-based coding environment |
| **SSH** | File management and debugging | Full command-line access |
| **Instance Portal** | Managing services | Application manager dashboard |

### **Service Management**
- **Supervisor** manages all background services
- Easy commands: `supervisorctl status`, `supervisorctl restart unsloth-studio`
- Add your own services with simple configuration files

### **Task Scheduling**
- **Cron** is enabled for automating routine tasks
- Schedule model downloads, cleanup scripts, or maintenance tasks
- Just add entries to your crontab to get started

### **Instance Control**
- **Vast.ai CLI** comes pre-installed with instance-specific API key
- Stop your instance from within itself: `vastai stop instance $CONTAINER_ID`
- Perfect for automated shutdown after training completes

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `UNSLOTH_STUDIO_ARGS` | `--host 127.0.0.1 --port 18888` | Unsloth Studio startup arguments |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### **Recommended GPU Memory**
| Use Case | Minimum VRAM | Recommended VRAM |
|----------|--------------|------------------|
| 7B model (QLoRA 4-bit) | 8 GB | 16 GB |
| 7B model (LoRA 16-bit) | 16 GB | 24 GB |
| 13B model (QLoRA 4-bit) | 16 GB | 24 GB |
| 70B model (QLoRA 4-bit) | 24 GB | 48 GB+ |

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `v0.1.0-cuda-12.9-py312`). This does not mean you need that exact CUDA version on the host.

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

- **Unsloth Documentation:** [Official Repository](https://github.com/unslothai/unsloth)
- **Unsloth Studio Announcement:** [GitHub Discussion](https://github.com/unslothai/unsloth/discussions/4370)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/unsloth-studio)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
