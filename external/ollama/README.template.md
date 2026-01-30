# Ollama
> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Ollama)**

## What is this template?

This template gives you a **hosted Ollama server** running in a Docker container. Ollama makes it easy to run large language models locally with a simple API. It supports a wide range of models and provides both a native API and an OpenAI-compatible endpoint. It's perfect for AI development, experimentation, or adding LLMs to your applications.

**Think:** *"Your own private AI server that can run any popular open-source model with a single command."*

> **Latest builds:** Docker images are automatically rebuilt when new Ollama releases are detected. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Run popular models** like Llama 3.1, Mistral, Gemma, Phi, and many more
- **Pull any model** from the Ollama model library with a single command
- **Send API requests** using Ollama's native API or OpenAI-compatible endpoints
- **Integrate with your applications** via REST API
- **Run multiple models** on the same instance
- **Access your API** from anywhere or via SSH tunnel
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Want to run open-source AI models without buying expensive hardware
- Need a simple API for your projects or business
- Are building applications that use large language models
- Want to experiment with different AI models quickly
- Need an OpenAI-compatible API backed by open-source models

---

## Quick Start Guide

### **Step 1: Configure Your Model**
Set the `OLLAMA_MODEL` environment variable with your desired model:
- **Any Ollama model:** Use the model name (e.g., `llama3.1:8b`, `mistral`, `gemma2:9b`)

**Optional configuration:**
- `OLLAMA_ARGS`: Additional arguments to pass to `ollama serve`
- `OLLAMA_EXTRA_MODELS`: Space-separated list of additional models to pull via provisioning

> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Ollama)"** when you've found an instance that works for you

### **Step 3: Wait for Setup**
Ollama will start and your chosen model will be pulled automatically *(this might take a few minutes depending on model size)*

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
     -d '{"model":"llama3.1:8b","messages":[{"role":"user","content":"Hello!"}]}' \
     http://<INSTANCE_IP>:<MAPPED_PORT_11434>/v1/chat/completions
```
*Use `https://` instead of `http://` if you've enabled HTTPS in your account settings.*

---

## Key Features

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Web Interface** | Quick testing | Click "Open" button |
| **API Calls** | Development | Use Bearer token |
| **SSH Tunnel** | Local development | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) to port 21434 |

### **Finding Your Token**
Your authentication token is available as `OPEN_BUTTON_TOKEN` in your instance environment. You can find it by:
- SSH: `echo $OPEN_BUTTON_TOKEN`
- Jupyter terminal: `echo $OPEN_BUTTON_TOKEN`

### **API Endpoints**
Ollama provides both native and OpenAI-compatible endpoints:
```bash
# Chat completions (OpenAI-compatible)
curl -H 'Authorization: Bearer <TOKEN>' \
     -H 'Content-Type: application/json' \
     -d '{"model":"llama3.1:8b","messages":[{"role":"user","content":"Hello"}]}' \
     http://<INSTANCE_IP>:<MAPPED_PORT_11434>/v1/chat/completions

# Generate (Ollama native)
curl -H 'Authorization: Bearer <TOKEN>' \
     -d '{"model":"llama3.1:8b","prompt":"Once upon a time"}' \
     http://<INSTANCE_IP>:<MAPPED_PORT_11434>/api/generate

# List models
curl -H 'Authorization: Bearer <TOKEN>' \
     http://<INSTANCE_IP>:<MAPPED_PORT_11434>/api/tags
```
*Use `https://` instead of `http://` if you've enabled HTTPS in your account settings.*

### **Interactive Chat**
Once connected via SSH or Jupyter terminal:
```bash
ollama run llama3.1:8b
```

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Ollama API | 11434 | 21434 |
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
- Easy commands: `supervisorctl status`, `supervisorctl restart ollama`
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
| `OLLAMA_MODEL` | (none) | Model to pull at startup (e.g., `llama3.1:8b`) |
| `OLLAMA_ARGS` | (none) | Extra arguments for `ollama serve` |
| `OLLAMA_HOST` | `0.0.0.0:21434` | Bind address for Ollama server |
| `OLLAMA_MODELS` | `$WORKSPACE/ollama/models` | Model storage path |
| `APT_PACKAGES` | (none) | Space-separated apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated Python packages to install on first boot |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

---

## Customization Tips

### **Installing Software**
```bash
# You have root access - install anything!
apt update && apt install -y your-favorite-package

# Install Python packages
pip install requests openai anthropic

# Add system services
echo "your-service-config" > /etc/supervisor/conf.d/my-app.conf
supervisorctl reread && supervisorctl update
```

### **Template Customization**
Want to save your perfect setup? Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later.

---

## Need More Help?

- **Ollama Documentation:** [Official Ollama Documentation](https://github.com/ollama/ollama/blob/main/docs/README.md)
- **Ollama Model Library:** [Available Models](https://ollama.com/library)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/external/ollama)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console

updated 20260129
