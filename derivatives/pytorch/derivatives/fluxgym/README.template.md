# FluxGym

> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=FluxGym)**

## What is this template?

This template gives you a **FLUX LoRA training environment** with FluxGym running in a Docker container. FluxGym wraps Kohya `sd-scripts` with a simple Gradio UI so you can train FLUX LoRAs from your own datasets without writing command-line configs by hand.

**Think:** *"Train FLUX LoRAs through a web UI — upload images, caption, train, download."*

> **Latest builds:** Docker images are automatically rebuilt when new upstream commits are detected. The default template tag is updated less frequently to allow for QA testing.

---

## What can I do with this?

- **Train FLUX LoRAs** from your own image datasets
- **Auto-caption** training data with Florence-2 (pre-installed)
- **Monitor training** through the Gradio UI
- **Resume** interrupted runs
- **Download** trained LoRAs directly from the UI

---

## Who is this for?

This is **perfect** if you:
- Want to train custom FLUX LoRAs without wrestling with Kohya config files
- Prefer a web UI for dataset management and training runs
- Need a reproducible GPU environment with the right PyTorch + `transformers` + `peft` combo pre-pinned

---

## Quick Start Guide

### **Step 1: Launch Instance**
Click **"[Rent](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=FluxGym)"** when you've found a suitable GPU instance.

### **Step 2: Access Your Instance**
Click the **"Open"** button to reach the FluxGym UI.

### **Step 3: Train**
Upload your dataset, configure the training parameters, and start the run. Trained LoRAs appear in the output folder when finished.

---

## Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| FluxGym | 17860 | 17860 |
| Jupyter | 8080 | 8080 |

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Workspace directory |
| `FLUXGYM_PORT` | `17860` | Gradio server port |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

---

### **Recommended GPU Memory**
| Use Case | Minimum VRAM | Recommended VRAM |
|----------|--------------|------------------|
| FLUX LoRA training | 16 GB | 24 GB+ |

---

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `<ref>-<date>-cuda-12.9`). This does not mean you need that exact CUDA version on the host.

**Minor version compatibility:** NVIDIA guarantees that an application built with any CUDA toolkit within a major version family will run on a driver from the same family. A `cuda-12.9` image runs on any CUDA 12.x driver (driver >= 525), and a `cuda-13.1` image runs on any CUDA 13.x driver (driver >= 580). The 12.x and 13.x families are separate.

**Forward compatibility:** All images include the [CUDA Compatibility Package](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) for use on **datacenter GPUs**.

---

## Need More Help?

- **FluxGym Repository:** [cocktailpeanut/fluxgym](https://github.com/cocktailpeanut/fluxgym)
- **Kohya sd-scripts:** [kohya-ss/sd-scripts](https://github.com/kohya-ss/sd-scripts)
- **Image Source & Features:** [GitHub Repository](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/fluxgym)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **SSH Setup Guide:** [Vast.ai SSH Documentation](https://docs.vast.ai/instances/sshscp)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
