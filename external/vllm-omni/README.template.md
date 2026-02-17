# vLLM-Omni Inference Engine
> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=vLLM-Omni)**

## What is this template?

This template gives you a **hosted vLLM-Omni API server** running in a Docker container. vLLM-Omni extends vLLM with omni-modality capabilities, letting you serve multimodal models that handle text, images, audio, and more through a simple REST API. It's perfect for AI development, production deployments, or adding high-performance multimodal LLMs to your applications.

**Think:** *"Your own private, high-performance multimodal AI API that you control completely."*

> **Latest builds:** Docker images are automatically rebuilt when new vLLM-Omni releases are detected. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Serve popular multimodal models** with optimized performance and throughput
- **Load any compatible model** from HuggingFace or local storage
- **Send API requests** for text generation, image generation, text-to-speech, and more
- **Integrate with your applications** via REST API
- **Scale with multiple GPUs** automatically using tensor parallelism
- **Access your API** from anywhere or via SSH tunnel
- **Monitor with Ray Dashboard** for distributed workload visibility
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Want to run multimodal AI models with maximum performance without buying expensive hardware
- Need a high-throughput API for your projects or business
- Are building production applications that use omni-modality models (text, vision, audio, TTS, diffusion)
- Want to experiment with different multimodal models with optimized inference
- Need programmatic access to LLMs with OpenAI-compatible endpoints

---

## Quick Start Guide

### **Step 1: Configure Your Model**
Set the `VLLM_MODEL` environment variable with your desired model:
- **Any HuggingFace model:** Use the full model path (e.g., `Qwen/Qwen3-Omni-30B-A3B-Instruct`)

**Optional configuration:**
- `VLLM_ARGS`: Additional arguments to pass to the vllm serve command
- `AUTO_PARALLEL`: Set to `true` (default) to automatically use all available GPUs with tensor parallelism

> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=vLLM-Omni)"** when you've found an instance that works for you

### **Step 3: Wait for Setup**
vLLM-Omni and your chosen model will download and start automatically *(this might take a few minutes depending on model size)*

### **Step 4: Access Your Instance**
**Easy access:** Just click the **"Open"** button - authentication is handled automatically!

**For external API calls:** If you want to make requests from outside (like curl commands), you'll need:
- **API Endpoint:** Your instance IP with the mapped external port (e.g., `http://123.45.67.89:45678` or `https://` if enabled)
- **Auth Token:** Auto-generated when your instance starts - see the **"Finding Your Token"** section below

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings. If you don't enable HTTPS, we'll **try** to redirect you to a temporary secure Cloudflare link (though availability is occasionally limited).

### **Step 5: Make Your First Request**
```bash
curl -X POST -H 'Authorization: Bearer <YOUR_TOKEN>' \
     -H 'Content-Type: application/json' \
     -d '{"model":"<MODEL_NAME>","messages":[{"role":"user","content":"Hello!"}]}' \
     http://<INSTANCE_IP>:<MAPPED_PORT_8000>/v1/chat/completions
```
*Use `https://` instead of `http://` if you've enabled HTTPS in your account settings.*

---

## Key Features

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Web Interface** | Quick testing | Click "Open" button |
| **API Calls** | Development | Use Bearer token |
| **SSH Tunnel** | Local development | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) to port 18000 |

### **Finding Your Token**
Your authentication token is available as `OPEN_BUTTON_TOKEN` in your instance environment. You can find it by:
- SSH: `echo $OPEN_BUTTON_TOKEN`
- Jupyter terminal: `echo $OPEN_BUTTON_TOKEN`

### **API Endpoints**
vLLM-Omni provides OpenAI-compatible endpoints:
```bash
# Chat completions
curl -H 'Authorization: Bearer <TOKEN>' \
     -H 'Content-Type: application/json' \
     -d '{"model":"<MODEL_NAME>","messages":[{"role":"user","content":"Hello"}]}' \
     http://<INSTANCE_IP>:<MAPPED_PORT_8000>/v1/chat/completions

# Text completions
curl -H 'Authorization: Bearer <TOKEN>' \
     -H 'Content-Type: application/json' \
     -d '{"model":"<MODEL_NAME>","prompt":"Once upon a time"}' \
     http://<INSTANCE_IP>:<MAPPED_PORT_8000>/v1/completions

# List models
curl -H 'Authorization: Bearer <TOKEN>' \
     http://<INSTANCE_IP>:<MAPPED_PORT_8000>/v1/models
```
*Use `https://` instead of `http://` if you've enabled HTTPS in your account settings.*

### **Model UI (Built-in Testing Interface)**
A lightweight web UI for quickly testing your model is included and accessible via the Instance Portal. It auto-detects your model type and shows the relevant tabs (chat, image, video, TTS). Use it to verify your model is working, experiment with parameters, and inspect raw API payloads.

> **For production or heavy use**, point your preferred local client (Open WebUI, SillyTavern, ComfyUI, etc.) at the API endpoint instead. The Model UI is a quick-test tool — dedicated interfaces will give you better performance and more features. Set `ENABLE_UI=false` to disable it.

### **Chat from the Command Line**
Once connected via SSH or Jupyter terminal:
```bash
vllm chat --url http://localhost:18000/v1
```

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Model UI | 7860 | 17860 |
| vLLM-Omni API | 8000 | 18000 |
| Ray Dashboard | 8265 | 28265 |
| Jupyter | 8080 | 8080 |

### **Instance Portal (Application Manager)**
- Web-based dashboard for managing your applications
- Cloudflare tunnels for easy sharing (no port forwarding needed!)
- Log monitoring for running services
- Start and stop services with a few clicks

### **Ray Dashboard**
- Monitor your distributed vLLM-Omni workloads
- View cluster status and resource utilization
- Access via Instance Portal or directly on port 8265

### **Dynamic Provisioning**
Need specific software installed automatically? Set the `PROVISIONING_SCRIPT` environment variable to a plain-text script URL (GitHub, Gist, etc.), and we'll run your setup script on first boot!

### **Quick Package Installation**
For simpler setups, use these environment variables:
- `APT_PACKAGES`: Space-separated list of apt packages to install on first boot
- `PIP_PACKAGES`: Space-separated list of Python packages to install on first boot

### **Multiple Access Methods**
| Method | Best For | What You Get |
|--------|----------|--------------|
| **Jupyter** | Interactive development | Browser-based coding environment |
| **SSH** | Terminal work | Full command-line access |
| **Instance Portal** | Managing services | Application manager dashboard |

### **Service Management**
- **Supervisor** manages all background services
- Easy commands: `supervisorctl status`, `supervisorctl restart vllm-omni`
- Add your own services with simple configuration files

### **Task Scheduling**
- **Cron** is enabled for automating routine tasks
- Schedule model downloads, API health checks, or maintenance tasks
- Just add entries to your crontab to get started

### **Instance Control**
- **Vast.ai CLI** comes pre-installed with instance-specific API key
- Stop your instance from within itself: `vastai stop instance $CONTAINER_ID`
- Perfect for automated shutdown based on API usage or conditions

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_MODEL` | (none) | Model to serve (e.g., `Qwen/Qwen3-Omni-30B-A3B-Instruct`) |
| `VLLM_ARGS` | (none) | Arguments passed to `vllm serve` |
| `AUTO_PARALLEL` | `true` | Automatically add `--tensor-parallel-size $GPU_COUNT` |
| `ENABLE_UI` | `true` | Enable the Model UI web interface on port 7860 |
| `RAY_ARGS` | `--head --port 6379 --dashboard-host 127.0.0.1 --dashboard-port 28265` | Arguments passed to `ray start` |
| `APT_PACKAGES` | (none) | Space-separated apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated Python packages to install on first boot |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### **Complex Arguments**
For arguments that are difficult to pass via environment variables (JSON strings, special characters, etc.), write them to `/etc/vllm-args.conf`. The contents of this file are appended to `$VLLM_ARGS` when launching vLLM-Omni.

Example template on start:
```bash
echo '--guided-decoding-backend lm-format-enforcer --chat-template-content-format string' > /etc/vllm-args.conf;
entrypoint.sh
```

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `v0.14.0-cuda-12.9`). This does not mean you need that exact CUDA version on the host.

**Minor version compatibility:** NVIDIA guarantees that an application built with any CUDA toolkit within a major version family will run on a driver from the same family. A `cuda-12.9` image runs on any CUDA 12.x driver (driver >= 525), and a `cuda-13.0` image runs on any CUDA 13.x driver (driver >= 580). The 12.x and 13.x families are separate.

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

- **vLLM-Omni Documentation:** [Official vLLM-Omni Documentation](https://docs.vllm.ai/projects/vllm-omni/en/latest)
- **vLLM-Omni GitHub:** [GitHub Repository](https://github.com/vllm-project/vllm-omni)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/external/vllm-omni)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console

updated 20260212
