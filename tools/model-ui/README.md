# Model UI

Lightweight web interface for vLLM, vLLM-Omni, and SGLang inference backends. Single-page HTML app with a Starlette proxy — no heavy UI frameworks.

## Files

| File | Purpose |
|---|---|
| `app.py` | Starlette server — waits for the backend API, detects the model type, injects config into the HTML, and proxies `/api/*` requests to the inference backend |
| `index.html` | HTML template — server injects model config at startup |
| `app.js` | Client-side JavaScript (tabs, chat, generation, history) |
| `style.css` | Styles with light/dark mode support |
| `requirements.txt` | Python deps (most are already present in vLLM/SGLang images) |

## Tabs

| Tab | API endpoint | Use case |
|---|---|---|
| **Chat** | `/v1/chat/completions` | Text, multimodal, and audio conversation (streaming for text, non-streaming when audio output is requested). Supports image and audio file attachments. |
| **Image** | `/v1/chat/completions` | Image generation/editing (Flux, SDXL, etc.) — sends a chat completion with image content in the response |
| **Video** | `/v1/chat/completions` | Video generation/editing (Wan, HunyuanVideo, etc.) — sends a chat completion with video content in the response |
| **TTS** | `/v1/audio/speech` | Text-to-speech (CosyVoice, etc.) |
| **STT** | `/v1/audio/transcriptions` | Speech-to-text (Whisper, etc.) — multipart form data |

The default tab is auto-detected from the model name, or set explicitly with `MODEL_UI_DEFAULT_TAB`. Setting tabs to `omni` maps to `chat`.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `VLLM_API_BASE` | `http://localhost:18000` | Inference backend URL |
| `MODEL_NAME` | _(empty)_ | Fallback model name for tab detection if the API hasn't loaded yet |
| `MODEL_UI_DEFAULT_TAB` | _(auto)_ | Force default tab: `chat`, `image`, `video`, `tts`, or `stt` (`omni` maps to `chat`). Legacy alias: `UI_MODE` |
| `MODEL_UI_CHAT_CAPS` | — | Chat capabilities (see below). Setting this makes the Chat tab visible |
| `MODEL_UI_IMAGE_CAPS` | — | Image capabilities: `generate`, `edit`, or both. Makes Image tab visible |
| `MODEL_UI_VIDEO_CAPS` | — | Video capabilities: `generate`, `edit`, or both. Makes Video tab visible |
| `MODEL_UI_TTS_CAPS` | — | TTS capabilities. Makes TTS tab visible |
| `MODEL_UI_STT_CAPS` | — | STT capabilities. Makes STT tab visible |

### Per-Tab Capabilities

The `MODEL_UI_<TAB>_CAPS` env vars serve two purposes: they control which tabs are visible and tailor each tab's UI to what the model actually supports. If **no** caps env vars are set, all tabs are shown with full defaults. If **any** are set, only tabs with caps are shown.

Use the special value `all` to show a tab with all default capabilities (no restrictions).

**Chat capabilities** control which output modality checkboxes are visible and pre-checked. The special `require_attach` token enforces that the user must attach a file before sending.

| Value | Effect |
|---|---|
| `all` | Tab visible, all modality checkboxes available (default behavior) |
| `text,image` | Only Text and Image checkboxes visible, both pre-checked |
| `image,require_attach` | Only Image checkbox visible; user must attach a file to send |

**Image/Video capabilities** control the generate vs. edit workflow:

| Value | Effect |
|---|---|
| `all` | Tab visible, dynamic generate/edit based on attachment |
| `generate` | Attachment field hidden entirely, button always says "Generate" |
| `edit` | Attachment required, button says "Edit", disabled until image attached |
| `generate,edit` | Same as `all` — dynamic "Generate"/"Edit" based on whether an image is attached |

### Examples

```bash
# Show only the chat tab with all defaults (e.g. base vLLM text model)
MODEL_UI_CHAT_CAPS=all

# Chat-based image editor — requires image attachment, image output only
MODEL_UI_CHAT_CAPS=image,require_attach

# Text-to-image model — chat for prompting, image tab for generation only
MODEL_UI_CHAT_CAPS=all
MODEL_UI_IMAGE_CAPS=generate

# Image editing model — always require an input image on the image tab
MODEL_UI_IMAGE_CAPS=edit

# Omni model — chat with text + audio output, plus image and video generation
MODEL_UI_CHAT_CAPS=text,audio
MODEL_UI_IMAGE_CAPS=generate
MODEL_UI_VIDEO_CAPS=generate

# Video generation only — no img2vid/vid2vid
MODEL_UI_VIDEO_CAPS=generate
```

## Features

- **Multimodal chat** — Attach images or audio files to chat messages; select an output modality (text, audio, image, video) in settings for models that support non-text responses (e.g. vLLM-Omni). Image and video responses are rendered inline; audio plays automatically.
- **Thinking/reasoning model support** — `<think>`, `<thinking>`, `<|think|>` blocks (and the `reasoning_content` API field) are rendered in a collapsible section and excluded from conversation history
- **Generation history** — Image, video, and TTS results persist in IndexedDB across page refreshes
- **Lightbox** — Click any image thumbnail to view full-size; navigate with arrow keys or prev/next buttons
- **Dark mode** — Follows system preference, consistent with the instance portal theme

## Running

```bash
# Expects an OpenAI-compatible API at VLLM_API_BASE
pip install httpx uvicorn starlette
python app.py
# → http://127.0.0.1:17860
```
