## vLLM-Omni (this image)

Runs vLLM-Omni (multimodal, OpenAI-compatible) — service label **"vLLM-Omni API"**.

- Served model is set by `VLLM_MODEL` (falls back to `MODEL_NAME`).
- A Ray dashboard is available as the "Ray Dashboard" service.
- For the externally callable `base_url` + auth: `curl -s http://localhost:11111/capabilities/endpoints`.
