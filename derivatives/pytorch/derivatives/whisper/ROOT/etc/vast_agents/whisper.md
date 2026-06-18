## Whisper (this image)

The PyTorch image plus a preinstalled **Whisper-WebUI** (upstream `jhj0517/Whisper-WebUI`)
for speech-to-text. Everything in base.md and pytorch.md applies unchanged (torch is in
`/venv/main`); this file covers what Whisper adds. **Two** services — get their externally
callable URLs + token from the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # both services, with direct_url + state
```

### whisper-api — programmatic transcription (the one agents want)

FastAPI backend (`uvicorn backend.main:app`), supervisor service **`whisper-api`**, internal
`0.0.0.0:8000`. This is the path for transcribing audio in code.

**It is NOT OpenAI-compatible — do not call `/v1/audio/transcriptions`.** This server has its
own routes; the transcription endpoint is **`POST /transcription`** (upload the audio file).
The full request/response schema is the source of truth at **`/docs`** (the OpenAPI page on
the same port) — read it before building a request, since options (model, language,
transcribe-vs-translate, diarization) are passed there rather than via OpenAI-style fields.
Launch flags are in **`WHISPER_API_ARGS`** (default `--host 0.0.0.0 --port 8000`).

**Language gotcha (will save you a hang):** the `lang` field wants the full lowercase language
**name** — `english`, not the ISO code `en`. A wrong value raises `KeyError` *inside a background
task*, so the request doesn't fail cleanly: the job **silently hangs at `progress: 0.0` with the
GPU idle** and never flips to `failed`. Omit `lang` (or use the auto-detect option) to let Whisper
detect the language. If a job sits at 0.0 doing nothing, check the backend log — that's where the
real error is.

### whisper-ui — interactive (service "whisper-ui")

A **Gradio** UI, supervisor service **`whisper-ui`**, internal `127.0.0.1:7860` — drag in audio,
pick model/language/task. Flags are in **`WHISPER_UI_ARGS`** (default
`--whisper_type whisper --server_port 7860`). It **waits for `whisper-api` to answer on `/docs`
before starting**, so on a fresh boot the UI can lag the API.

### Models & provisioning

Runs from `${WORKSPACE}/Whisper-WebUI` in `/venv/main`. Whisper model weights download on first
use and cache to the Hugging Face cache (`HF_HOME`); the model size/variant is selected through
the UI or the API request, not a dedicated env var. Add anything declaratively with the base
provisioner (`PROVISIONING_SCRIPT`, base.md §10). **Both services wait for provisioning
(`/.provisioning`) to finish before starting**, so during boot they may be intentionally down —
check that flag before assuming a fault.
