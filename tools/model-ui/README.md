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
| **Chat** | `/v1/chat/completions` | Text conversation with streaming. Supports image and audio file attachments for vision-language models. Always sends `modalities: ["text"]` |
| **Image** | `/v1/images/generations`, `/v1/images/edits` | Image generation/editing (Flux, SDXL, etc.). Optional "Use chat API" toggle routes through `/v1/chat/completions` instead (required for BAGEL and other chat-based diffusion models) |
| **Video** | `/v1/videos` | Video generation/editing (Wan, HunyuanVideo, etc.) — multipart form data |
| **TTS** | `/v1/audio/speech` | Text-to-speech — standard OpenAI TTS, plus Qwen3-TTS modes (VoiceDesign, VoiceClone) via vLLM-Omni |
| **STT** | `/v1/audio/transcriptions` | Speech-to-text (Whisper, etc.) — multipart form data |

The default tab is auto-detected from the model name, or set explicitly with `MODEL_UI_DEFAULT_TAB`. Setting tabs to `omni` maps to `chat`.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MODEL_UI_API_BASE` | `http://localhost:18000` | Inference backend URL |
| `MODEL_NAME` | _(empty)_ | Fallback model name for tab detection if the API hasn't loaded yet |
| `MODEL_UI_DEFAULT_TAB` | _(auto)_ | Force default tab: `chat`, `image`, `video`, `tts`, or `stt` (`omni` maps to `chat`). Legacy alias: `UI_MODE` |
| `MODEL_UI_CHAT_CAPS` | — | Chat capabilities (see below). Setting this makes the Chat tab visible |
| `MODEL_UI_IMAGE_CAPS` | — | Image capabilities: `generate`, `edit`, or both. Makes Image tab visible |
| `MODEL_UI_VIDEO_CAPS` | — | Video capabilities: `generate`, `edit`, or both. Makes Video tab visible |
| `MODEL_UI_TTS_CAPS` | — | TTS capabilities (see below). Makes TTS tab visible |
| `MODEL_UI_STT_CAPS` | — | STT capabilities. Makes STT tab visible |
| `MODEL_UI_PROMPT_WRAPPER` | — | Prompt wrapper template applied to all outgoing prompts. Use `{prompt}` as the placeholder. Example: `<\|im_start\|>{prompt}<\|im_end\|>` (required for BAGEL) |

### Per-Tab Capabilities

The `MODEL_UI_<TAB>_CAPS` env vars serve two purposes: they control which tabs are visible and tailor each tab's UI to what the model actually supports. If **no** caps env vars are set, all tabs are shown with full defaults. If **any** are set, only tabs with caps are shown.

Use the special value `all` to show a tab with all default capabilities (no restrictions).

**Chat capabilities** control the chat tab. The special `require_attach` token enforces that the user must attach a file before sending.

| Value | Effect |
|---|---|
| `all` | Tab visible with defaults |
| `require_attach` | User must attach a file before sending |

**Image/Video capabilities** control the generate vs. edit workflow:

| Value | Effect |
|---|---|
| `all` | Tab visible, dynamic generate/edit based on attachment |
| `generate` | Attachment field hidden entirely, button always says "Generate" |
| `edit` | Attachment required, button says "Edit", disabled until image attached |
| `generate,edit` | Same as `all` — dynamic "Generate"/"Edit" based on whether an image is attached |
| `chat_api` | Pre-selects the "Use chat API" toggle (routes through `/v1/chat/completions` instead of dedicated image endpoints). Combine with other values, e.g. `chat_api,generate` |

**TTS capabilities** control the TTS mode for Qwen3-TTS models served via vLLM-Omni. Standard OpenAI-compatible TTS models work with the default or `custom_voice` mode.

| Value | Effect |
|---|---|
| `all` | All modes available: Standard, VoiceDesign, VoiceClone — mode selector shown |
| `custom_voice` | Standard TTS (voice dropdown, speed, instructions) — no `task_type` sent |
| `voice_design` | VoiceDesign mode — describe the voice in natural language, sends `task_type: "VoiceDesign"` |
| `voice_clone` | VoiceClone mode — upload or record reference audio + optional transcript, sends `task_type: "Base"` |
| `custom_voice,voice_design` | Standard and VoiceDesign modes, mode selector shown |
| `custom_voice,voice_design,voice_clone` | Same as `all` |

When multiple modes are configured, a mode selector appears at the top of the TTS panel. VoiceClone supports both file upload and microphone recording; reference audio is converted to WAV client-side for backend compatibility.

### Examples

```bash
# Show only the chat tab with all defaults (e.g. base vLLM text model)
MODEL_UI_CHAT_CAPS=all

# Text-to-image model — chat for prompting, image tab for generation only
MODEL_UI_CHAT_CAPS=all
MODEL_UI_IMAGE_CAPS=generate

# Image editing model — always require an input image on the image tab
MODEL_UI_IMAGE_CAPS=edit

# BAGEL or other chat-based diffusion model — use chat API for image gen, wrap prompts
MODEL_UI_CHAT_CAPS=all
MODEL_UI_IMAGE_CAPS=chat_api,generate
MODEL_UI_PROMPT_WRAPPER="<|im_start|>{prompt}<|im_end|>"

# Omni model — chat plus image and video generation
MODEL_UI_CHAT_CAPS=all
MODEL_UI_IMAGE_CAPS=generate
MODEL_UI_VIDEO_CAPS=generate

# Vision-language model that requires image attachment
MODEL_UI_CHAT_CAPS=require_attach

# Video generation only — no img2vid/vid2vid
MODEL_UI_VIDEO_CAPS=generate

# Standard TTS model (CosyVoice, etc.)
MODEL_UI_TTS_CAPS=custom_voice

# Qwen3-TTS VoiceDesign model — describe the voice you want
MODEL_UI_TTS_CAPS=voice_design

# Qwen3-TTS VoiceClone model — clone from reference audio
MODEL_UI_TTS_CAPS=voice_clone

# Qwen3-TTS with all modes available
MODEL_UI_TTS_CAPS=all
```

## Features

- **Multimodal chat** — Attach images or audio files to chat messages for vision-language and audio understanding models. Text-only output with streaming.
- **Thinking/reasoning model support** — `<think>`, `<thinking>`, `<|think|>` blocks (and the `reasoning_content` API field) are rendered in a collapsible section and excluded from conversation history
- **Generation history** — Image, video, and TTS results persist in IndexedDB across page refreshes
- **Lightbox** — Click any image thumbnail to view full-size; navigate with arrow keys or prev/next buttons
- **Dark mode** — Follows system preference, consistent with the instance portal theme

## Running

```bash
# Expects an OpenAI-compatible API at MODEL_UI_API_BASE
pip install httpx uvicorn starlette
python app.py
# → http://127.0.0.1:17860
```
