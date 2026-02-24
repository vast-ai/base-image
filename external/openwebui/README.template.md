# Open WebUI
> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Open+WebUI)**

## What is this template?

This template gives you **Open WebUI with a built-in Ollama server** running in a Docker container. Open WebUI is a modern, feature-rich web interface for interacting with large language models. Combined with Ollama, you get a complete self-hosted ChatGPT-like experience with support for a wide range of open-source models.

**Think:** *"Your own private ChatGPT that runs any popular open-source model, with a beautiful web interface and full API access."*

> **Latest builds:** Docker images are automatically rebuilt when new Open WebUI releases are detected. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Chat with AI models** through a polished web interface (like ChatGPT)
- **Run popular models** like Llama 3.1, Mistral, Gemma, Phi, and many more
- **Pull any model** from the Ollama model library
- **Create custom model presets** with system prompts and parameters
- **Upload documents** for RAG (Retrieval-Augmented Generation)
- **Manage conversations** with history, search, and organization
- **Connect external APIs** like OpenAI, together with local Ollama models
- **Access the Ollama API** directly for programmatic use
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Want a ChatGPT-like experience with open-source models
- Need a user-friendly interface without writing API calls
- Want to experiment with different AI models quickly
- Need document upload and RAG capabilities
- Want both a web UI and API access to your models
- Are building or prototyping AI applications

---

## Quick Start Guide

### **Step 1: Configure Your Model**
Set the `OLLAMA_MODEL` environment variable with your desired model:
- **Any Ollama model:** Use the model name (e.g., `llama3.1:8b`, `mistral`, `gemma2:9b`)

**Optional configuration:**
- `OPENAI_API_KEY`: Add an OpenAI API key to use OpenAI models alongside local ones
- `OPENAI_API_BASE_URL`: Point to any OpenAI-compatible API

> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Open+WebUI)"** when you've found an instance that works for you

### **Step 3: Wait for Setup**
Ollama will start first and pull your chosen model automatically. Open WebUI launches once Ollama is fully ready *(this might take a few minutes depending on model size)*

### **Step 4: Access Open WebUI**
**Easy access:** Just click the **"Open"** button - authentication is handled automatically!

You'll see the Open WebUI interface where you can:
- Start chatting immediately with your loaded model
- Pull additional models from the admin panel
- Upload documents for RAG
- Configure model parameters and system prompts

**For external API calls:** If you want to make Ollama API requests from outside, you'll need:
- **API Endpoint:** Your instance IP with the mapped Ollama port (e.g., `http://123.45.67.89:45678`)
- **Auth Token:** Auto-generated when your instance starts - see the **"Finding Your Token"** section below

> **HTTPS Option:** Want secure connections? Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page. You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings. If you don't enable HTTPS, we'll **try** to redirect you to a temporary secure Cloudflare link (though availability is occasionally limited).

---

## Key Features

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Web Interface** | Chat & model management | Click "Open" button |
| **API Calls** | Development | Use Bearer token |
| **SSH Tunnel** | Local development | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) to port 17500 (WebUI) or 21434 (Ollama) |

### **Finding Your Token**
Your authentication token is available as `OPEN_BUTTON_TOKEN` in your instance environment. You can find it by:
- SSH: `echo $OPEN_BUTTON_TOKEN`
- Jupyter terminal: `echo $OPEN_BUTTON_TOKEN`

### **Ollama API Endpoints**
The Ollama API is also accessible for programmatic use:
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

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Open WebUI | 7500 | 17500 |
| Ollama API | 11434 | 21434 |
| Jupyter | 8080 | 18080 |

### **Data Persistence**
Open WebUI stores its database, uploads, and cache in `$WORKSPACE/data`. This persists across container restarts. Ollama models are stored in `$WORKSPACE/ollama/models`.

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
| **Open WebUI** | Chat & model management | Full-featured web UI |
| **Jupyter** | Interactive development | Browser-based coding environment |
| **SSH** | Terminal work | Full command-line access |
| **Instance Portal** | Managing services | Application manager dashboard |

### **Service Management**
- **Supervisor** manages all background services
- Easy commands: `supervisorctl status`, `supervisorctl restart ollama`, `supervisorctl restart open_webui`
- Add your own services with simple configuration files

### **Task Scheduling**
- **Cron** is enabled for automating routine tasks
- Schedule model downloads, API health checks, or maintenance tasks
- Just add entries to your crontab to get started

### **Instance Control**
- **Vast.ai CLI** comes pre-installed with instance-specific API key
- Stop your instance from within itself: `vastai stop instance $CONTAINER_ID`
- Perfect for automated shutdown based on usage or conditions

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | (none) | Model to pull at startup (e.g., `llama3.1:8b`) |
| `OLLAMA_ARGS` | (none) | Extra arguments for `ollama serve` |
| `OLLAMA_HOST` | `0.0.0.0:11434` | Bind address for Ollama server |
| `OLLAMA_MODELS` | `$WORKSPACE/ollama/models` | Model storage path |
| `OPEN_WEBUI_DATA_DIR` | `$WORKSPACE/data` | Open WebUI data directory |
| `WEBUI_SECRET_KEY` | (auto-generated) | Secret key for session encryption |
| `OPENAI_API_KEY` | (none) | OpenAI API key for external models |
| `OPENAI_API_BASE_URL` | (none) | Custom OpenAI-compatible API URL |
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

- **Open WebUI Documentation:** [Official Docs](https://docs.openwebui.com/)
- **Ollama Model Library:** [Available Models](https://ollama.com/library)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/external/openwebui)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console

updated 20260224
