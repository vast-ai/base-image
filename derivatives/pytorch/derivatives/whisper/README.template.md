# Whisper (API + UI)

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Whisper)**

## What is this template?

This template gives you a **speech-to-text environment** with both the Whisper-WebUI Gradio interface **and** its FastAPI backend, running side-by-side in a Docker container. Use the Web UI for interactive transcription / translation, or hit the API from your own applications.

**Think:** *"Transcribe audio and video with OpenAI Whisper — through a friendly UI or a REST API."*

> **Latest builds:** Docker images are automatically rebuilt when new Whisper-WebUI releases are detected. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Transcribe audio / video** to text using Whisper (tiny → large-v3)
- **Translate** audio in any supported language to English
- **Use the Gradio UI** for drag-and-drop transcription with speaker diarization
- **Call the REST API** from your own apps, scripts, or workflows
- **Access** via SSH, Jupyter, or Instance Portal

---

## Who is this for?

This is **perfect** if you:
- Want an out-of-the-box Whisper service without wrestling with dependencies
- Need both a human-friendly UI and a machine-friendly API in one instance
- Prefer a reproducible GPU environment with PyTorch pre-installed

---

## Quick Start Guide

### **Step 1: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Whisper)"** when you've found a suitable GPU instance.

### **Step 2: Access Your Instance**
Click the **"Open"** button to reach the Whisper WebUI, or use the API endpoint directly (see port reference below).

### **Step 3: Transcribe**
- **UI:** upload a file, choose a model, click Transcribe.
- **API:** POST audio to `/transcription` on port 18000.

---

## Services

| Service | Description |
|---------|-------------|
| **Whisper WebUI** | Gradio front-end, starts after the API is reachable |
| **Whisper API** | FastAPI backend exposing transcription / translation endpoints |

Each runs as its own Supervisor process. To run **only** one of them, remove the other's entry from `/etc/portal.yaml` — its startup script will exit cleanly.

---

## Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Whisper WebUI | 17860 | 7860 |
| Whisper API | 18000 | 8000 |
| Jupyter | 8080 | 8080 |

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Workspace directory |
| `WHISPER_API_ARGS` | `--host 0.0.0.0 --port 8000` | uvicorn args |
| `WHISPER_UI_ARGS` | `--whisper_type whisper --server_port 7860` | Gradio args |
| `WHISPER_API_HEALTH_URL` | `http://localhost:8000/docs` | URL the UI polls before starting |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `<ref>-<date>-cuda-12.9`). This does not mean you need that exact CUDA version on the host.

**Minor version compatibility:** NVIDIA guarantees that an application built with any CUDA toolkit within a major version family will run on a driver from the same family. A `cuda-12.9` image runs on any CUDA 12.x driver (driver >= 525), and a `cuda-13.1` image runs on any CUDA 13.x driver (driver >= 580). The 12.x and 13.x families are separate.

**Forward compatibility:** All images include the [CUDA Compatibility Package](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) for use on **datacenter GPUs**.

---

## Need More Help?

- **Whisper-WebUI Repository:** [jhj0517/Whisper-WebUI](https://github.com/jhj0517/Whisper-WebUI)
- **OpenAI Whisper:** [openai/whisper](https://github.com/openai/whisper)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/whisper)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
