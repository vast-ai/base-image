## Voicebox (this image)

The PyTorch image plus a preinstalled **Voicebox** (upstream `jamiepine/voicebox`) — a local
voice studio: clone a voice from a few seconds of audio (or use 50+ preset voices), generate
speech across several TTS engines, and transcribe. Everything in base.md and pytorch.md applies
unchanged (torch is in `/venv/main`); this file covers what Voicebox adds. It is **not** an OpenAI
`/v1` endpoint. Get the externally callable URL + token from the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # the service, with direct_url + state
```

### Use the supported interfaces — do NOT patch the package

One service, **`voicebox`** (`python -m backend.main` from `/opt/voicebox`), internal
`127.0.0.1:17493` (flags in `VOICEBOX_ARGS`), serves the web UI, a REST API, **and an MCP server**
on that single port. Synthesis, cloning, and transcription are all driven through the API/MCP by
choosing a voice **profile** and **engine** — you never need to edit the TTS engine code or the
Python package to make it work. If an engine errors, switch engines (below); don't patch internals.

### MCP server — the best path for an agent (mounted at /mcp)

Voicebox ships a built-in Model Context Protocol server (Streamable HTTP) at
**`http://127.0.0.1:17493/mcp`**. Add it and call tools instead of hand-rolling HTTP. On the box:
```
claude mcp add voicebox --transport http \
  --url http://127.0.0.1:17493/mcp \
  --header "X-Voicebox-Client-Id: claude-code"
```
The header is just a label for the per-client voice binding (not a secret). Off-box, use the
service's authed `direct_url` from the manifest instead of `127.0.0.1`, or SSH-forward 17493.
stdio-only clients can point at the bundled shim **`/opt/voicebox/voicebox-mcp`**. Four tools:
- **`voicebox.list_profiles`** — available voices (cloned + preset). Start here.
- **`voicebox.speak({text, profile?, engine?, language?})`** — synthesize; returns a
  `generation_id` + `poll_url` (`/generate/<id>/status`); fetch the finished audio when it's done.
- **`voicebox.transcribe(...)`** — Whisper STT of a local path or base64 clip.
- **`voicebox.list_captures`** — recent captures, paginated.

### REST API (equivalent, for non-MCP callers)

Same port; full schema at **`/docs`**:
```
GET  /profiles                    # list voices (cloned + preset)
POST /generate                    # synthesize -> poll /generate/<id>/status, then fetch the file
POST /speak    {text, profile}    # speak with a profile (name or id)
POST /transcribe                  # speech-to-text
```

### Voices, engines, cloning, data

**Preset voices** (e.g. **Kokoro**) need no reference audio — pick a preset profile and synthesize.
**Cloning** is zero-shot from a short reference clip: register it as a profile, then synthesize with
that profile. Engines: `qwen` / `qwen_custom_voice` / `luxtts` / `chatterbox` / `chatterbox_turbo` /
`tada` / `kokoro` (choose via the `engine` arg); **engine model weights download on first use**, so
the first call is slow — expected, not a hang. Persistent state — the DB, voice profiles, and
generated audio — lives under **`VOICEBOX_DATA_DIR`** (default `${WORKSPACE}/voicebox-data`). **The
service waits for provisioning (`/.provisioning`) to finish before starting**, so during boot it may
be intentionally down — check that flag before assuming a fault. (This image is built amd64-only.)
