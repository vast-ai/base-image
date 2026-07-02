# Chatterbox Image

A Vast.ai image for chatterbox. Includes the Instance Portal, Supervisor process
management, and other conveniences from the Vast.ai
[base image](https://github.com/vast-ai/base-image).

## How it works

Bundles [Chatterbox-TTS-Server](https://github.com/devnen/Chatterbox-TTS-Server) (a
FastAPI wrapper over Resemble AI's Chatterbox TTS/voice-clone models) on top of the
Vast PyTorch base. The server is installed at `/opt/chatterbox` and started via
supervisor (`supervisorctl start chatterbox`), bound to `127.0.0.1:8004` behind Caddy.

- **Web UI + API** at port 8004: a browser UI, a custom `/tts` endpoint, and an
  OpenAI-compatible `/v1/audio/speech`.
- **Models** download on first synthesis into `/opt/chatterbox/model_cache` (nothing is
  baked into the image). The default `chatterbox-turbo` needs ~4 GB VRAM; Original /
  Multilingual ~8 GB. Some model repos are HF-gated — set `HF_TOKEN` to use them.
- **Output is watermarked** with Resemble AI's imperceptible Perth watermark (by design).

**Config:** `/opt/chatterbox/config.yaml` (host is pinned to loopback at build). **Tags:**
`<ref>-cuda-<ver>-py312` per the build matrix. **System deps:** `libsndfile1`, `ffmpeg`.
