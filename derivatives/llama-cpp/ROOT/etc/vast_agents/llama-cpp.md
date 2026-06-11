## llama.cpp (this image)

The base image plus a preinstalled **llama.cpp** CUDA server (`llama-server`).
Everything in base.md (supervisor, Caddy auth edge, ports, storage, GPU/CUDA,
provisioning) applies unchanged — this file covers only what llama.cpp adds. Unlike the
vLLM/SGLang images there is **no Model UI and no Ray service**: `llama-server` ships its
own built-in web UI and serves the OpenAI API from the *same* process and port.

### Serving a model

The server runs as the supervisor service **`llama`** (portal label **"Llama.cpp
UI"**). Opening that portal entry gives `llama-server`'s built-in chat UI; the **same**
endpoint also serves the OpenAI API. Get its externally callable `base_url` + auth from
the capability manifest rather than guessing the port (base.md §9):
```
curl -s http://localhost:11111/capabilities/endpoints   # base_url, capabilities (chat/completions/embeddings/models), auth
```
Internally it listens on `127.0.0.1:18000`; the web UI is at `/` and `/v1` is the
OpenAI base. Call `/v1` exactly like the OpenAI API, with the instance token (base.md §5).

**The model is chosen by `LLAMA_MODEL`** and loaded via `llama-server -hf` — so it is a
**Hugging Face GGUF repo** (e.g. `ggml-org/gemma-3-4b-it-GGUF`, or a
`…-GGUF` repo with a specific quant like `unsloth/Qwen3-8B-GGUF:Q4_K_M`), **not** a raw
transformers checkpoint. With no model set the service just logs *"Model not
specified"* and idles. It is normally set at instance creation via the template's env
(`LLAMA_MODEL` or its alias `MODEL_NAME` — linked at boot).

To **change the model persistently**, set `LLAMA_MODEL` where the instance sources env
on every boot, then restart. `/etc/environment` survives stop/start; `${WORKSPACE}/.env`
additionally survives recycle/destroy *if* a volume is mounted (base.md §3, §8):
```
echo 'LLAMA_MODEL="ggml-org/gemma-3-4b-it-GGUF"' >> /etc/environment
supervisorctl restart llama
```
Downloaded GGUFs are cached under `${WORKSPACE}/llama.cpp` (symlinked from
`~/.cache/llama.cpp`), so they persist on a volume and survive a restart.

### Tuning llama-server (`LLAMA_ARGS`)

Extra `llama-server` flags go in **`LLAMA_ARGS`** (e.g. `--ctx-size`, `--n-gpu-layers`,
`--parallel`, `--flash-attn`, or `-m /path/to/local.gguf` to serve a local file instead
of `-hf`).

> **Gotcha:** `LLAMA_ARGS` *replaces* the default `--port 18000`. If you set it, you
> **must** re-include `--port 18000`, or the server binds elsewhere and the
> "Llama.cpp UI" portal mapping breaks. Example:
> `LLAMA_ARGS="--port 18000 --ctx-size 8192 --n-gpu-layers 999"`.

### The binaries and CUDA

`llama-server` (and `llama-cli`, `llama-bench`, …) are prebuilt CUDA binaries under
`/opt/llama.cpp/cuda-<ver>` (on `PATH` via `LLAMA_CPP_DIR`) — they are **not** a Python
package, so there is no venv to activate for the server itself (the base image's
`/venv/main` and Python kernel are still present for your own use). The build links
against the system CUDA runtime (`libcublas` is installed for the image's CUDA version)
rather than bundling its own, and the same binaries cover the full set of GPU compute
capabilities.
