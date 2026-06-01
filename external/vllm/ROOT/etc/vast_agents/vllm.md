## vLLM (this image)

Runs vLLM as an OpenAI-compatible server — service label **"vLLM API"**.

- Served model is set by `VLLM_MODEL` (falls back to `MODEL_NAME`).
- A Ray dashboard is available as the "Ray Dashboard" service.
- For the externally callable `base_url` + auth: `curl -s http://localhost:11111/capabilities/endpoints`.
