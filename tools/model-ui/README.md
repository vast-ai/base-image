# Model UI

Lightweight web interface for vLLM, vLLM-Omni, and SGLang inference backends. Single-page HTML app with a Starlette proxy — no heavy UI frameworks.

## Files

| File | Purpose |
|---|---|
| `app.py` | Starlette server — waits for the backend API, detects the model type, injects config into the HTML, and proxies `/api/*` requests to the inference backend |
| `index.html` | Self-contained SPA (HTML + CSS + JS) |
| `requirements.txt` | Python deps (most are already present in vLLM/SGLang images) |

## Tabs

| Tab | API endpoint | Use case |
|---|---|---|
| **Chat** | `/v1/chat/completions` | Text, multimodal, and audio conversation (streaming for text, non-streaming when audio output is requested). Supports image and audio file attachments. |
| **Image** | `/v1/images/generations` | Image generation (Flux, SDXL, etc.) |
| **Video** | `/v1/videos` | Video generation (Wan, HunyuanVideo, etc.) — multipart form data |
| **TTS** | `/v1/audio/speech` | Text-to-speech (CosyVoice, etc.) |

The default tab is auto-detected from the model name, or set explicitly with `UI_MODE`. Setting `UI_MODE=omni` maps to the Chat tab.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `VLLM_API_BASE` | `http://localhost:18000` | Inference backend URL |
| `MODEL_NAME` | _(empty)_ | Fallback model name for tab detection if the API hasn't loaded yet |
| `UI_MODE` | _(auto)_ | Force default tab: `chat`, `image`, `video`, or `tts` (`omni` maps to `chat`) |
| `MODEL_UI_TABS` | _(all)_ | Comma-delimited list of tabs to show (e.g. `image,video`). Unset = show all |

## Features

- **Multimodal chat** — Attach images or audio files to chat messages; select an output modality (text, audio, image, video) in settings for models that support non-text responses (e.g. vLLM-Omni). Image and video responses are rendered inline; audio plays automatically.
- **Thinking/reasoning model support** — `<think>`, `<thinking>`, `<|think|>` blocks (and the `reasoning_content` API field) are rendered in a collapsible section and excluded from conversation history
- **Generation history** — Image, video, and TTS results persist in localStorage across page refreshes
- **Lightbox** — Click any image thumbnail to view full-size; navigate with arrow keys or prev/next buttons
- **Request payload editor** — Every tab exposes the raw JSON payload; edit directly or reset to form values
- **Dark mode** — Follows system preference, consistent with the instance portal theme

## Running

```bash
# Expects an OpenAI-compatible API at VLLM_API_BASE
pip install httpx uvicorn starlette
python app.py
# → http://127.0.0.1:17860
```
