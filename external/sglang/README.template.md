# SGLang Inference Engine
> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=SGLang)**

## What is this template?

This template gives you a **hosted SGLang API server** running in a Docker container. SGLang (Structured Generation Language) is a fast serving framework for large language models with advanced features like RadixAttention for efficient prefix caching and structured output generation. It's perfect for AI development, production deployments, or adding high-performance LLMs to your applications.

**Think:** *"Your own private, high-performance ChatGPT API with structured generation capabilities that you control completely."*

---

## What can I do with this?

- **Serve popular LLMs** with optimized performance and throughput
- **Load any compatible model** from HuggingFace or local storage
- **Send API requests** to generate text with OpenAI-compatible endpoints
- **Integrate with your applications** via REST API
- **Scale with multiple GPUs** automatically using tensor parallelism
- **Access your API** from anywhere or via SSH tunnel
- **Leverage RadixAttention** for efficient prefix caching and batch processing
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Want to run AI models with maximum performance without buying expensive hardware
- Need a high-throughput API for your projects or business
- Are building production applications that use large language models
- Want to experiment with different AI models with optimized inference
- Need programmatic access to LLMs with OpenAI-compatible endpoints
- Want advanced features like constrained decoding and structured outputs

---

## Quick Start Guide

### **Step 1: Configure Your Model**
Set the `SGLANG_MODEL` environment variable with your desired model:
- **Any HuggingFace model:** Use the full model path (e.g., `meta-llama/Llama-3.1-8B-Instruct`)

**Optional configuration:**
- `SGLANG_ARGS`: Additional arguments to pass to the sglang serve command
- `AUTO_PARALLEL`: Set to `true` (default) to automatically use all available GPUs with tensor parallelism

> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=SGLang)"** when you've found an instance that works for you

### **Step 3: Wait for Setup**
SGLang and your chosen model will download and start automatically *(this might take a few minutes depending on model size)*

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
SGLang provides OpenAI-compatible endpoints:
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

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| SGLang API | 8000 | 18000 |
| Jupyter | 8080 | 8080 |

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
- Easy commands: `supervisorctl status`, `supervisorctl restart sglang`
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
| `SGLANG_MODEL` | (none) | Model to serve (e.g., `meta-llama/Llama-3.1-8B-Instruct`) |
| `SGLANG_ARGS` | (none) | Arguments passed to `sglang serve` |
| `AUTO_PARALLEL` | `true` | Automatically add `--tensor-parallel-size $GPU_COUNT` |
| `APT_PACKAGES` | (none) | Space-separated apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated Python packages to install on first boot |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### **Complex Arguments**
For arguments that are difficult to pass via environment variables (JSON strings, special characters, etc.), write them to `/etc/sglang-args.conf`. The contents of this file are appended to `$SGLANG_ARGS` when launching SGLang.

Example template on start:
```bash
echo '--chat-template chatml' > /etc/sglang-args.conf;
entrypoint.sh
```

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

- **SGLang Documentation:** [Official SGLang Documentation](https://docs.sglang.ai/)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/external/sglang)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console

updated 20251222
