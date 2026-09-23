# Kev templates (provisioning manifest)

[Kev](https://github.com/jaredpalmer/kev) is a family of open "System One" decision models: a state (text or
JSON) and typed questions in (`choice`, `noul` yes/no, `score`), a probability for every option out, in one
forward pass with no text generated. It serves TypeSafe's `/v1/systemone` API, so TypeSafe's SDKs work against
it unchanged. Why Kev and not the other open alternatives is recorded in
[ADR 0046](../../docs/adr/0046-kev-system-one-templates.md).

This directory is one provisioning manifest for the stock `vastai/pytorch` -mini image, and two templates
that differ only in the model and the GPU floor.

| Path | What it is |
|---|---|
| `kev.yaml` | The manifest: clones Kev and this repo at pinned commits, installs the locked deps, registers three supervisor services, pre-downloads the model |
| `requirements-kev.txt` | Kev server deps for `/venv/main`: exact versions + hashes, no torch (the image's is used) |
| `requirements-ui.txt` | Deps for the UI's own venv `/venv/kev-ui`: gradio 6.28.0, no torch |
| `prefetch.py` | Downloads the pinned adapter and the exact base revision it was trained on, during provisioning |
| `ui/` | The Kev Hugging Face Space, adapted to call the local API instead of loading a second copy of the model |
| `templates/kev-4b`, `templates/kev-9b` | `template.yml` + marketplace `README.md` (ADR 0011 format) |

## What runs

| Portal entry | Local bind | Public (via Caddy) | What it is |
|---|---|---|---|
| Kev API | `127.0.0.1:18000` | `8000`, opens `/docs` | `kev.serve`: `/v1/systemone`, `/v1/models`, plus `/permute` and `/separate` |
| Kev Playground | `127.0.0.1:13000` | `3000` | Kev's Next.js playground (presets, option-order test, packed vs separate, chess demo); proxies `/kev/*` to the API itself |
| Kev UI | `127.0.0.1:17860` | `7860` | The adapted Space (Gradio): answer cards with probability bars, option-order check |

Nothing binds a public interface; Caddy's auth fronts all three. The front-ends are HTTP clients of the
one Kev server and add no GPU memory.

## Pins (nothing follows a moving branch)

| Pin | Where | Value |
|---|---|---|
| Base image | template `tag` | `2.8.0-cu128-cuda-12.9-mini-py313-2026-09-08` (Kev needs torch < 2.9; tested on Python 3.13) |
| This manifest and its files | template `PROVISIONING_MANIFEST` URL + `KEV_TEMPLATE_REF` | the same base-image commit; the manifest refuses to run if the checkout does not match |
| Kev source | `kev.yaml` | `jaredpalmer/kev@557598fced1dada75dfbf36ed144dce309ac6ceb` |
| Python deps | `requirements-*.txt` | exact versions + sha256 hashes, installed `--no-deps --require-hashes` |
| Model | template `KEV_MODEL` | `jaredpalmer/kev-4b@485ace87…` / `jaredpalmer/kev-9b@2629c06a…`; the base (`Qwen/Qwen3.5-*-Base`) revision comes from the adapter's own metadata |
| Playground npm deps | Kev's `package-lock.json` at the pinned commit | `npm ci` |
| Node | the image's nvm LTS | fixed by the dated image tag |
| UI presets | `ui/presets.py` | vendored from the Space at revision `46ada90f…` |

The manifest asserts torch is still the image's 2.8.0 after the Kev install, so an upstream change that tries
to replace torch fails provisioning instead of silently shipping a different stack.

## Measured (2026-09-23, RTX PRO 6000, card sizes emulated with a per-process CUDA allocation cap)

| Model | `KEV_MERGE=1` (Kev's default) | `KEV_MERGE=0` (these templates) |
|---|---|---|
| Kev-4B | ~18.6 GB (fp32 load + merge on the GPU) | fits 12 GB; accuracy 0.797 vs 0.799 on Kev's transfer-v4; ~30% slower |
| Kev-9B | ~34 GB | fits 24 GB; identical accuracy; ~30% slower |

Booted from the published templates (same GPU, 2026-09-23):

| Template | VRAM in use | Disk used | Provisioning | First user request |
|---|---|---|---|---|
| Kev-4B | 9.6 GB | 11 GB | ~1 min on a fast-download host | 0.8 s (after the automatic warm-up), then ~60 ms |
| Kev-9B | 16.7 GB | 21 GB | ~1.5 min on a fast-download host | 0.8 s, then ~60 ms |

Provisioning time is dominated by the model download (9 GB / 19 GB) and scales with the host's bandwidth.

Kev's server loads the base in fp32 on the GPU, merges the LoRA adapter, then casts to bf16, so its peak is
about 4 bytes per parameter. `KEV_MERGE=0` loads bf16 directly with the adapter unmerged. On a card with room
to spare, set `KEV_MERGE=1` in the template for the faster merged path.

Accuracy against TypeSafe's hosted Jev on the same items (Kev's recorded live-Jev runs): Kev-9B trails by
3-8 points on held-out suites and ties it on support-ticket routing; the large gap is knowledge questions
(MMLU-Pro 0.52 vs 0.84), which is set by the base model.

## Limits users should know

- One request at a time: the server does not batch across callers. This is an evaluation and development
  template, not a high-throughput deployment.
- Context: 8,192 tokens for the state plus one question (trained on shorter). Longer requests get HTTP 422.
- Option order can change an answer; the option-order check in both front-ends measures it.
- The Gradio UI streams results over server-sent events; Cloudflare quick tunnels are known to buffer those.
  Use the direct portal link.

## Changing a pin

1. Bump the value (Kev commit in `kev.yaml`, a lock file, `KEV_MODEL`, or the image `tag`).
2. For a Kev bump, regenerate `requirements-kev.txt` as its header describes and re-vendor `ui/presets.py`
   if the Space's presets changed.
3. Commit, then point both templates' `KEV_TEMPLATE_REF` and `PROVISIONING_MANIFEST` URL at that commit.
4. Boot each template on a real GPU and check all three portal entries answer through Caddy.

After a squash-merge the branch commit a template points at becomes unreachable once the branch is deleted,
so published templates must be re-pointed at the resulting `main` commit.
