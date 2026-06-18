## ACE-Step (this image)

The PyTorch image plus a preinstalled **ACE-Step** music/audio generation stack. Everything in
base.md and pytorch.md applies unchanged (torch is in `/venv/main`); this file covers what
ACE-Step adds. **Two** services, and the API is **not** an OpenAI `/v1` endpoint. Get the
externally callable URLs + token from the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # both services, with direct_url + state
```

### ace-step-api — programmatic generation (service "ace-step-api")

A **FastAPI** server (`acestep-api`), supervisor service **`ace-step-api`**, internal
`127.0.0.1:8001`. This is the path to generate music in code. The endpoints + request schema
are the source of truth at **`/docs`** (the OpenAPI page on port 8001) — read it before building
a request; it is app-specific, not OpenAI-shaped.

### ace-step-ui — interactive (service "ace-step-ui")

A Node web app (`ace-step-ui`), supervisor service **`ace-step-ui`**, internal
`127.0.0.1:3000`. It **waits for the API's `/docs` to return 200 before starting**, so on a
fresh boot the UI lags the API.

### Models, outputs & provisioning

The app lives at `${WORKSPACE}/ACE-Step-1.5` and runs in `/venv/main`; generated audio lands
under `${WORKSPACE}`. The language model is selected by **`ACESTEP_LM_MODEL_PATH`** (default
`acestep-5Hz-lm-4B`); **model weights download on first generation**, so the first request is
slow — that is expected, not a hang. Add anything declaratively with the base provisioner
(`PROVISIONING_SCRIPT`, base.md §10). **Both services wait for provisioning (`/.provisioning`)
to finish before starting**, so during boot they may be intentionally down — check that flag
before assuming a fault. (This image is built amd64-only.)
