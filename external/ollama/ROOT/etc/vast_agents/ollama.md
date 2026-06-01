## Ollama (this image)

Runs Ollama — service label **"Ollama API"**, OpenAI-compatible at `/v1`.

- Manage models with the `ollama` CLI: `ollama list`, `ollama pull <model>`, `ollama run <model>`.
- For the externally callable `base_url` + auth: `curl -s http://localhost:11111/capabilities/endpoints`.
