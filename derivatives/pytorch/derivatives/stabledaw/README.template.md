# StableDAW
> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=stabledaw)**

## What is this template?

This template gives you a **complete AI music studio** in your browser, GPU-accelerated
on Vast.ai. StableDAW ([theDAW](https://github.com/gantasmo/theDAW)) is an all-in-one
digital audio workstation powered by the Stable Audio 3 diffusion engine: generate music
and audio from text, then arrange, edit, mix, and perform it — idea to finished track to
live set, all in one app.

**Think:** *"Generate a track from a prompt, then produce and perform it — a full AI DAW
on a cloud GPU."*

> **Latest builds:** Docker images are rebuilt from the latest upstream commit. To use a
> newer build before it becomes the template default, pick a specific tag from the
> **version tag dropdown** on the template configuration page.

---

## What can I do with this?

- **Text-to-audio generation** with Stable Audio 3 (the Medium model needs ~8 GB VRAM)
- **Full studio composition** — arrange, edit, mix, and master in the browser
- **Audio analysis, effects, and mastering** tools
- **MIDI transcription and notation** (audio → MIDI → MusicXML/tabs/score)
- **DJ and live-performance** tools with a continuous, harmony-aware playlist builder
- **A library** that auto-saves every render with its full generation settings
- **Terminal + Jupyter access** with root privileges for installing extra software

---

## Who is this for?

This is **perfect** if you:
- Want to generate original music and audio from text prompts on a GPU
- Are a producer, composer, or sound designer who wants AI in the workflow
- Need more VRAM than a local machine for the Medium model
- Want an integrated create → produce → perform pipeline instead of separate tools

---

## Quick Start Guide

### **Step 1: Launch and open the portal**
Create the instance, open the **Instance Portal**, and click the **StableDAW** tab. The
UI is served behind authentication on external port **8600**.

### **Step 2: Pick a model and generate**
StableDAW is **local-only by default** — nothing downloads at startup. The first time you
generate, allow the model download when prompted (or register a checkpoint already on
disk via **Settings → Models**).

### **Step 3: Produce and perform**
Move generated audio into the editor, mixer, and live tools. Every render is saved to the
library automatically.

> **Template Customization:** Templates can't be changed directly, but you can make your
> own version — click **edit**, change what you need, and save it to **"My Templates"**.
> [Full guide here](https://docs.vast.ai/templates)
