## Voicebox (this image)

The PyTorch image plus a preinstalled **Voicebox** (upstream `jamiepine/voicebox`) — a
text-to-speech / voice studio. Everything in base.md and pytorch.md applies unchanged (torch is
in `/venv/main`); this file covers what Voicebox adds. One service, and it is **not** an OpenAI
`/v1` endpoint. Get the externally callable URL + token from the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # the service, with direct_url + state
```

### The app — web UI + REST API on one port (service "voicebox")

Supervisor service **`voicebox`** (`python -m backend.main` from `/opt/voicebox`), internal
`127.0.0.1:17493`. Like InvokeAI, the **same port serves both the web UI and a FastAPI backend**,
always on — the browser app calls that backend. The OpenAPI schema is the source of truth at
**`/docs`**: read it to drive synthesis (and voice profiles) programmatically rather than through
the UI. Launch flags are in **`VOICEBOX_ARGS`** (default `--host 127.0.0.1 --port 17493`).

### Data, models & provisioning

Persistent state — the database, voice profiles, and generated audio — lives under
**`VOICEBOX_DATA_DIR`** (default `${WORKSPACE}/voicebox-data`), so it survives on the workspace;
the app runs in `/venv/main`. Add anything declaratively with the base provisioner
(`PROVISIONING_SCRIPT`, base.md §10). **The service waits for provisioning (`/.provisioning`) to
finish before starting**, so during boot it may be intentionally down — check that flag before
assuming a fault. (This image is built amd64-only.)
