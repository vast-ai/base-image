# Llama.cpp

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Llama.cpp)**

## What is this template?

This template gives you a **high-performance llama.cpp inference server** running in a Docker container. Llama.cpp is a fast, lightweight runtime for large language models in GGUF format. It provides an OpenAI-compatible API, a built-in web UI, and supports a huge range of models from HuggingFace.

**Think:** *"A lightweight, high-performance AI server that runs GGUF models with minimal overhead and maximum speed."*

> **Latest builds:** Docker images are automatically rebuilt when new llama.cpp releases are detected. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Run popular models** like Llama 3.1, Mistral, Gemma, Phi, Qwen, and many more in GGUF format
- **Load any GGUF model** directly from HuggingFace with a single environment variable
- **Send API requests** using the OpenAI-compatible API
- **Use the built-in web UI** for interactive chat
- **Choose quantization levels** to balance quality vs. memory usage
- **Access your API** from anywhere or via SSH tunnel
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Want a lightweight, fast inference server for open-source LLMs
- Need OpenAI-compatible API endpoints for your applications
- Want fine-grained control over quantization and model loading
- Prefer GGUF models for their efficiency and flexibility
- Need to run models that fit tightly into available GPU memory
- Want a simple setup with minimal dependencies

---

## Quick Start Guide

### **Step 1: Configure Your Model**
Set the `LLAMA_MODEL` environment variable with your desired HuggingFace model:
- **Any GGUF model:** Use the HuggingFace repository path (e.g., `bartowski/Meta-Llama-3.1-8B-Instruct-GGUF`)

**Optional configuration:**
- `LLAMA_ARGS`: Additional arguments to pass to `llama-server` (default: `--port 18000`)

> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Llama.cpp)"** when you've found an instance that works for you

### **Step 3: Wait for Setup**
The llama-server will start and your chosen model will be downloaded automatically *(this might take a few minutes depending on model size)*

### **Step 4: Access Your Instance**
**Easy access:** Just click the **"Open"** button - authentication is handled automatically!

**For external API calls:** If you want to make requests from outside (like curl commands), you'll need:
- **API Endpoint:** Your instance IP with the mapped external port (e.g., `http://123.45.67.89:45678` or `https://` if enabled)
- **Auth Token:** Auto-generated when your instance starts - see the **"Finding Your Token"** section below

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings. If you don't enable HTTPS, we'll **try** to redirect you to a temporary secure Cloudflare link (though availability is occasionally limited).

### **Step 5: Make Your First Request**
```bash
curl -H 'Authorization: Bearer <YOUR_TOKEN>' \
     -H 'Content-Type: application/json' \
     -d '{"model":"model","messages":[{"role":"user","content":"Hello!"}]}' \
     http://<INSTANCE_IP>:<MAPPED_PORT_8000>/v1/chat/completions
```
*Use `https://` instead of `http://` if you've enabled HTTPS in your account settings.*

---

## Key Features

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Web UI** | Interactive chat | Click "Open" button |
| **API Calls** | Development | Use Bearer token |
| **SSH Tunnel** | Local development | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) to port 18000 |

### **Finding Your Token**
Your authentication token is available as `OPEN_BUTTON_TOKEN` in your instance environment. You can find it by:
- SSH: `echo $OPEN_BUTTON_TOKEN`
- Jupyter terminal: `echo $OPEN_BUTTON_TOKEN`

### **API Endpoints**
Llama.cpp provides OpenAI-compatible endpoints:
```bash
# Chat completions (OpenAI-compatible)
curl -H 'Authorization: Bearer <TOKEN>' \
     -H 'Content-Type: application/json' \
     -d '{"model":"model","messages":[{"role":"user","content":"Hello"}]}' \
     http://<INSTANCE_IP>:<MAPPED_PORT_8000>/v1/chat/completions

# Text completion
curl -H 'Authorization: Bearer <TOKEN>' \
     -H 'Content-Type: application/json' \
     -d '{"prompt":"Once upon a time"}' \
     http://<INSTANCE_IP>:<MAPPED_PORT_8000>/completion

# List models
curl -H 'Authorization: Bearer <TOKEN>' \
     http://<INSTANCE_IP>:<MAPPED_PORT_8000>/v1/models
```
*Use `https://` instead of `http://` if you've enabled HTTPS in your account settings.*

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Llama.cpp UI | 8000 | 18000 |
| Jupyter | 8080 | 18080 |

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
| **Jupyter** | Interactive development | Browser-based coding environment |
| **SSH** | Terminal work | Full command-line access |
| **Instance Portal** | Managing services | Application manager dashboard |

### **Service Management**
- **Supervisor** manages all background services
- Easy commands: `supervisorctl status`, `supervisorctl restart llama`
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
| `LLAMA_MODEL` | (none) | HuggingFace model to load at startup (e.g., `bartowski/Meta-Llama-3.1-8B-Instruct-GGUF`) |
| `LLAMA_ARGS` | `--port 18000` | Extra arguments for `llama-server` |
| `APT_PACKAGES` | (none) | Space-separated apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated Python packages to install on first boot |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `b5460-cuda-12.9`). This does not mean you need that exact CUDA version on the host.

**Minor version compatibility:** NVIDIA guarantees that an application built with any CUDA toolkit within a major version family will run on a driver from the same family. A `cuda-12.9` image runs on any CUDA 12.x driver (driver >= 525), and a `cuda-13.1` image runs on any CUDA 13.x driver (driver >= 580). The 12.x and 13.x families are separate.

**Forward compatibility:** All images include the [CUDA Compatibility Package](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html), which allows newer CUDA toolkit versions to run on older drivers. This is only available on **datacenter GPUs** (e.g., H100, A100, L40S, RTX Pro series) -- for example, a `cuda-12.9` image can run on a datacenter host with a CUDA 12.1 driver. Consumer GPUs do not support forward compatibility and require a driver that natively supports the CUDA version.

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

- **llama.cpp Documentation:** [Official Repository](https://github.com/ggml-org/llama.cpp)
- **llama.cpp Server Reference:** [Server Documentation](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
- **GGUF Models:** [HuggingFace GGUF Models](https://huggingface.co/models?library=gguf)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/llama-cpp)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
