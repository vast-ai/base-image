# Wan2GP

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Wan2GP)**

## What is this template?

This template gives you a **low-VRAM video generation environment** with Wan2GP running in a Docker container. Wan2GP is a Gradio UI for Wan-family video diffusion models, optimised to run on GPUs that would normally struggle with the full pipeline.

**Think:** *"Generate high-quality AI video clips on consumer GPUs."*

> **Latest builds:** Docker images are rebuilt automatically on a periodic schedule, and are only pushed when the resolved upstream ref produces a new tag. The default template tag is updated less frequently to allow for QA testing. To use a newly built image before it becomes the template default, select a specific version from the **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Generate videos** from text or image prompts using Wan 2.x family models
- **Run on lower-VRAM GPUs** thanks to Wan2GP's memory optimisations
- **Iterate quickly** through the built-in Gradio UI
- **Manage models** directly on disk under `$WORKSPACE/Wan2GP`
- **Use SSH, Jupyter, or Instance Portal** to manage your instance

---

## Who is this for?

This is **perfect** if you:
- Want to experiment with Wan-family video models without renting the largest GPUs
- Prefer a simple Gradio UI over command-line tooling
- Need a reproducible environment with PyTorch and all dependencies pre-installed

---

## Quick Start Guide

### **Step 1: Configure Your Setup**
Set your preferred configuration via environment variables:
- **`WORKSPACE`**: Custom workspace directory
- **`WAN2GP_PORT`**: Override the default internal port (7860)
- **`WAN2GP_ARGS`**: Additional CLI flags for `wgp.py`
- **`PROVISIONING_SCRIPT`**: URL to auto-download dependencies on first boot

### **Step 2: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Wan2GP)"** when you've found a suitable GPU instance

### **Step 3: Wait for Setup**
Wan2GP will be ready automatically with all required dependencies pre-installed *(initial model downloads may take additional time)*

### **Step 4: Access Your Instance**
**Easy access:** Just click the **"Open"** button — authentication is handled automatically.

### **Step 5: Generate**
Open the Wan2GP UI, select a model, enter a prompt, and start generating.

---

## Key Features

### **Authentication & Access**
| Method | Use Case | Setup Required |
|--------|----------|----------------|
| **Web Interface** | All generation operations | Click "Open" button |
| **SSH Terminal** | System administration | [SSH access](https://docs.vast.ai/instances/sshscp) |
| **SSH Tunnel** | Bypass authentication | [SSH port forwarding](https://docs.vast.ai/instances/sshscp) to port 7860 |

### **Port Reference**
| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Wan2GP | 17860 | 7860 |
| Jupyter | 8080 | 8080 |

### **Service Management**
- **Supervisor** manages all background services
- Easy commands: `supervisorctl status`, `supervisorctl restart wan2gp`

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Workspace directory |
| `WAN2GP_PORT` | `7860` | Gradio server port |
| `WAN2GP_ARGS` | (none) | Extra CLI args appended to `python wgp.py` |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `<ref>-<date>-cuda-12.9`). This does not mean you need that exact CUDA version on the host.

**Minor version compatibility:** NVIDIA guarantees that an application built with any CUDA toolkit within a major version family will run on a driver from the same family. A `cuda-12.9` image runs on any CUDA 12.x driver (driver >= 525), and a `cuda-13.1` image runs on any CUDA 13.x driver (driver >= 580). The 12.x and 13.x families are separate.

**Forward compatibility:** All images include the [CUDA Compatibility Package](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html), which allows newer CUDA toolkit versions to run on older drivers on **datacenter GPUs**.

---

## Need More Help?

- **Wan2GP Repository:** [deepbeepmeep/Wan2GP](https://github.com/deepbeepmeep/Wan2GP)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/wan2gp)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
