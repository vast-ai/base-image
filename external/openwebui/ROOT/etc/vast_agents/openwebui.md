## Open WebUI (this image)

**Open WebUI** (a browser chat UI) backed by a **bundled Ollama** server on the same instance —
so this image both *serves* models and gives you a chat over them. base.md applies. Two
services; get their externally callable URLs + token from the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services    # both services, with direct_url + state
curl -s http://localhost:11111/capabilities/endpoints   # Ollama's OpenAI /v1 base_url + key
```

### The model is the thing you configure (start here)

The bundled **Ollama** (supervisor service `ollama`, internal `:21434`) is the model backend;
**Open WebUI** (service `open_webui`, internal `:17500`) is just the front end, already pointed at
it (`OLLAMA_BASE_URL=http://localhost:21434`). Two ways to set the served model:

- **At launch — `OLLAMA_MODEL`** (e.g. `llama3.2`, `qwen2.5:7b`): Ollama pulls it at boot and it
  appears in Open WebUI automatically. (`MODEL_NAME` is an accepted alias.)
- **At runtime — the `ollama` CLI:** `ollama pull <model>` / `ollama list` / `ollama rm <model>`;
  a newly pulled model shows up in Open WebUI's model selector right away.

Models persist at **`${WORKSPACE}/ollama/models`**, and Open WebUI's own data (accounts, chats) at
**`${WORKSPACE}/data`** — both survive on the workspace.

### Boot order & direct API access

**Open WebUI waits for Ollama to be up *and* the configured model pulled** (`/tmp/.ollama_ready`)
before it starts — so on first boot with a large `OLLAMA_MODEL` the chat UI is intentionally down
while the model downloads. That's the pull, not a fault. Besides the chat UI, Ollama is directly
usable for code: its native `/api` plus an **OpenAI-compatible `/v1`** (chat/completions,
embeddings). Take the `base_url` + key from `/capabilities/endpoints` to point an external client
(or another agent) straight at it.
