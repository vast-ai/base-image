## Open WebUI (this image)

Runs **Open WebUI** (chat UI — service "Open WebUI") backed by a bundled
**Ollama** server (service "Ollama API", OpenAI-compatible at `/v1`).

- Manage models with the `ollama` CLI: `ollama list`, `ollama pull <model>`.
- For the externally callable `base_url` + auth: `curl -s http://localhost:11111/capabilities/endpoints`.
