# Chatterbox

**What it is:** Chatterbox-TTS-Server (TTS + zero-shot voice cloning) at `/opt/chatterbox`.

**Start/stop:** `supervisorctl start chatterbox` / `stop chatterbox`. Logs:
`supervisorctl tail -f chatterbox`.

**Endpoint:** binds `127.0.0.1:8004` (reach it via the Caddy-proxied portal, not the raw
port). OpenAI-compatible `POST /v1/audio/speech` (requires `model`, `input`, `voice`);
also a Web UI at `/` and `/docs`. Readiness: `GET /v1/audio/voices` returns 200 once the
model is loaded (503 with "model is not currently loaded" until then).

**Synthesize (example):**
```
curl -X POST http://127.0.0.1:8004/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"tts-1","input":"Hello from Vast.","voice":"Emily.wav","response_format":"wav"}' -o out.wav
```

**Models/data:** weights download on first synth to `/opt/chatterbox/model_cache`. Set
`HF_TOKEN` for gated model repos. Config: `/opt/chatterbox/config.yaml`. Output audio
carries Resemble AI's Perth watermark.
