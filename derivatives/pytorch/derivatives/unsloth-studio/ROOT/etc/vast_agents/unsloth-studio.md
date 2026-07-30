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

### llama.cpp (GGUF)

The bundled `llama.cpp` is a **prebuilt CUDA binary** from the Unsloth fork, baked into the image
at **`/opt/llama-cpp`** (the studio's `~/.unsloth/llama.cpp` is a symlink to it). Which release is
baked in is readable from **`$LLAMA_CPP_RELEASE`**, and in full detail from
`/opt/llama-cpp/UNSLOTH_PREBUILT_INFO.json` (upstream tag + source commit). It deliberately lives
outside `${WORKSPACE}`, so it is image content and is repointed at the image's copy on every boot —
do not expect edits there to survive, and do not report a stale llama.cpp from a reused volume as a
bug without checking `$LLAMA_CPP_RELEASE` first. If GGUF inference looks slow, check that the CUDA
backend loaded (`ldd -r /opt/llama-cpp/build/bin/libggml-cuda.so` should report nothing missing
except the driver `libcuda.so.1`) — llama.cpp skips an unloadable backend and runs on CPU silently.

### Data, models, outputs

Studio state — datasets, runs, and **outputs (LoRA adapters / merged models / GGUF exports)** —
persists under **`${WORKSPACE}/unsloth`** (the app's `~/.unsloth` is repointed there at boot). For
a headless script, write outputs wherever you like under `${WORKSPACE}`. Base LLMs pull from the
Hugging Face Hub on first use; **`HF_TOKEN` is NOT pre-set here** — export it yourself for gated
models (e.g. Llama).

The app runs in `/venv/main` and **waits for provisioning (`/.provisioning`) to finish before
starting**, so during boot it may be intentionally down — check that flag before assuming a fault.
This image is built **amd64-only** (torchao has no aarch64 wheels yet).
