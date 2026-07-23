## Unsloth Studio (this image)

The PyTorch image plus a preinstalled **Unsloth Studio** (the AGPL "studio" component of the
`unsloth` library) — a web UI for fast **LLM fine-tuning** (Llama, Qwen, DeepSeek, Gemma,
Mistral, Phi …). Everything in base.md and pytorch.md applies unchanged (torch is in
`/venv/main`); this file covers what it adds. It is a training tool, **not** an inference/OpenAI
endpoint — the deliverable is a fine-tuned model / adapter / GGUF. Get the externally callable URL
+ token from the manifest (base.md §5, §9):
```
curl -s http://localhost:11111/capabilities/services   # the service, with direct_url + state
```

### The app — web UI (service "unsloth-studio")

Supervisor service **`unsloth-studio`** (`unsloth studio`, flags in **`UNSLOTH_STUDIO_ARGS`**,
default `--host 127.0.0.1 --port 18888`), internal `127.0.0.1:18888`. The studio has its **own
login** behind the Caddy gateway: on a fresh instance it is `unsloth` / **`password`**, and the
studio forces a password change on first login (a boot hook pre-seeds that known credential so
users need not read a random one off disk; safe because the gateway already gates access). Once a
user sets their own password it persists across stop/start and is never reset. It is **click-driven**:
pick a base model, upload/import a dataset, design a data recipe, set hyperparameters, hit Start
Training, watch loss curves / GPU usage, then export (incl. **GGUF** — a `llama.cpp` build is
bundled for conversion/inference). There is no training API to call.

### Driving a fine-tune headlessly

An agent can't click the UI, but the full **`unsloth` Python library is in the shared
`/venv/main`**, so it can fine-tune from a script — the standard
`FastLanguageModel.from_pretrained(...)` + TRL `SFTTrainer` pattern — run from a terminal or the
**Jupyter** service on this image (separate portal entry). No example notebooks/scripts ship here;
the supported turnkey path is the Studio UI, and headless is the usual library code.

### Data, models, outputs

Studio state — datasets, runs, and **outputs (LoRA adapters / merged models / GGUF exports)** —
persists under **`${WORKSPACE}/unsloth`** (the app's `~/.unsloth` is repointed there at boot). For
a headless script, write outputs wherever you like under `${WORKSPACE}`. Base LLMs pull from the
Hugging Face Hub on first use; **`HF_TOKEN` is NOT pre-set here** — export it yourself for gated
models (e.g. Llama).

The app runs in `/venv/main` and **waits for provisioning (`/.provisioning`) to finish before
starting**, so during boot it may be intentionally down — check that flag before assuming a fault.
This image is built **amd64-only** (torchao has no aarch64 wheels yet).
