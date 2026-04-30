# Voicebox

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Voicebox)**

## What is this template?

This template gives you a **complete text-to-speech environment** with Voicebox running in a Docker container. Voicebox provides a web interface and REST API for high-quality voice synthesis, voice cloning, and audio processing using multiple TTS engines.

**Think:** *"A production-ready voice synthesis server — clone voices, generate speech, and process audio from a browser or API!"*

> **Latest builds:** Docker images are automatically rebuilt when new Voicebox releases are detected. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

> **HTTPS Required for Microphone:** Voicebox's voice cloning feature uses the browser microphone API, which requires a secure context (HTTPS). Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page to enable this. Without HTTPS, you can still upload audio files for voice cloning, but direct microphone recording will not work.

---

## What can I do with this?

- **Text-to-speech generation** with multiple engines (Qwen3-TTS, Chatterbox, Kokoro, LuxTTS, TADA)
- **Voice cloning** from audio samples
- **Voice profile management** for consistent character voices
- **Audio effects processing** (reverb, EQ, compression, and more via Pedalboard)
- **Batch generation** and long-form audio with automatic chunking
- **REST API access** for integration with other tools and pipelines
- **Story/audiobook creation** with multi-voice track editing
- **Audio transcription** for voice-to-text workflows
- **Terminal access** with root privileges for installing additional software

---

## Who is this for?

This is **perfect** if you:
- Want high-quality AI voice synthesis on GPU
- Need voice cloning capabilities for content creation
- Are building audiobooks, podcasts, or voice content
- Want a REST API for TTS integration in your applications
- Need to generate speech in multiple voices consistently
- Are developing games, animations, or media with character dialogue
- Want to experiment with different TTS models and engines

---

## Quick Start Guide

### **Step 1: Configure Your Setup**
Set your preferred configuration via environment variables:
- **`WORKSPACE`**: Custom workspace directory for your data
- **`VOICEBOX_ARGS`**: Custom server arguments (host, port)
> **Template Customization:** Templates can't be changed directly, but you can easily make your own version! Just click **edit**, make your changes, and save it as your own template. You'll find it in your **"My Templates"** section later. [Full guide here](https://docs.vast.ai/templates)

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Voicebox)"** when you've found a suitable GPU instance

### **Step 3: Wait for Setup**
Voicebox will be ready automatically *(initial model downloads happen on first use and may take additional time)*

### **Step 4: Access Your Instance**
**Easy access:** Just click the **"Open"** button - authentication is handled automatically!

**For direct access:** If you want to connect from outside, you'll need:
- **Voicebox URL:** Your instance IP with the mapped external port
- **Auth Token:** Auto-generated when your instance starts - see the **"Finding Your Token"** section below

> **HTTPS:** Set `ENABLE_HTTPS=true` in the **Environment Variables section** of your Vast.ai account settings page. HTTPS is required for browser microphone access (used for voice cloning). You'll need to [install the Vast.ai certificate](https://docs.vast.ai/instances/jupyter) to avoid browser warnings.

### **Step 5: Start Creating**
Open the Voicebox web interface, create a voice profile, and start generating speech!

---

## Key Features

### **Multiple TTS Engines**
- **Qwen3-TTS** — High-quality multilingual synthesis
- **Chatterbox** — Fast voice cloning with natural prosody
- **Kokoro** — Lightweight 82M-parameter engine
- **LuxTTS** — Advanced voice cloning
- **TADA (HumeAI)** — Expressive speech synthesis

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Web Interface** | All Voicebox operations | Click "Open" button |
| **REST API** | Programmatic access | Use instance URL |
| **SSH Terminal** | System administration | [SSH access](https://docs.vast.ai/instances/sshscp) |
| **SSH Tunnel** | Bypass authentication | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) to port 17493 |

### **Finding Your Token**
Your authentication token is available as `OPEN_BUTTON_TOKEN` in your instance environment. You can find it by:
- SSH: `echo $OPEN_BUTTON_TOKEN`
- Jupyter terminal: `echo $OPEN_BUTTON_TOKEN`

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Voicebox | 17493 | 17493 |
| Jupyter | 8080 | 8080 |

### **Instance Portal (Application Manager)**
- Web-based dashboard for managing your applications
- Cloudflare tunnels for easy sharing (no port forwarding needed!)
- Log monitoring for running services
- Start and stop services with a few clicks

### **Multiple Access Methods**
| Method | Best For | What You Get |
|--------|----------|--------------|
| **Web Interface** | Voice generation and management | Full Voicebox UI |
| **REST API** | Automation and integration | 90+ API endpoints |
| **Jupyter** | Custom scripts and experimentation | Browser-based coding environment |
| **SSH** | File management and debugging | Full command-line access |
| **Instance Portal** | Managing services | Application manager dashboard |

### **Service Management**
- **Supervisor** manages all background services
- Easy commands: `supervisorctl status`, `supervisorctl restart voicebox`
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

## API Usage

Voicebox exposes a comprehensive REST API. Access the interactive docs at `http://<instance-url>/docs`.

### Example: Generate Speech
```bash
curl -X POST http://localhost:17493/generate \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world", "profile_id": "your-profile-id"}' \
  --output speech.wav
```

### Example: List Voice Profiles
```bash
curl http://localhost:17493/profiles
```

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Voicebox workspace directory |
| `VOICEBOX_ARGS` | `--host 127.0.0.1 --port 17493` | Voicebox server arguments |
| `VOICEBOX_DATA_DIR` | `${WORKSPACE}/voicebox-data` | Data directory for database, profiles, and audio |
| `VOICEBOX_CORS_ORIGINS` | (local origins) | Additional CORS origins, comma-separated |

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `v0.3.1-cuda-12.9-py312`). This does not mean you need that exact CUDA version on the host.

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

- **Voicebox Documentation:** [GitHub Repository](https://github.com/jamiepine/voicebox)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/voicebox)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
