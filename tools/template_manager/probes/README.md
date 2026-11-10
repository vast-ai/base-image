# Capability probes (imported as-is)

These are the ad-hoc probes used during model-library reviews (2026-10), committed
**unchanged** per ADR 0059, D7 step 1, so they are not lost. They have not yet been
adapted to this repo: their `sys.path` lines still point at a local vast_landing
checkout, and each probe reads `VAST_ADMIN_API_KEY` from `~/.env`. The shell
wrappers that held local paths were removed; run a probe with `python3`.

| Probe | What it checks |
|---|---|
| `mm_probe.py` | image (OCR token + colors), multi-image order, optional video (`--video`); retries at temperature 0 |
| `audio_probe.py` | TTS (valid WAV, `/v1/completions` crash check) and ASR (transcript vs reference) |
| `chat_probe.py` | single turn, system message, history with `reasoning_content`, plain history; reports which reasoning field the server returns |
| `embed_probe.py` | `/v1/embeddings` dimension, paraphrase ranks above an unrelated sentence |

`fixtures/voxcpm2-speech.wav` is speech we generated ourselves with the VoxCPM2
template: "Hello from Vast. The secret number is seven three one nine."

Next (ADR 0059, D7 step 3): rework these into capability-keyed probes run by
`test_template.py`, each with positive and negative offline fixture tests.
