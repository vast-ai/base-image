# ADR 0046 — Kev "System One" decision-model templates on the pytorch -mini image

- **Status:** Accepted
- **Date:** 2026-09-23
- **Decision owner:** Rob Ballantyne

## Context
TypeSafe's Jev (announced 2026-09-15) is a closed-weights "System One" model: a state plus typed questions
(`choice`, `noul` yes/no, `score`) in, a calibrated probability per option out, in one forward pass with no
text generation, served as `POST /v1/systemone`. It cannot run on Vast. Within a week there were dozens of
open reimplementations, of two kinds:

- **Trained decision models**: new weights (usually a LoRA plus a small decision head) behind the project's
  own server. Kev, Open-Jev and Laya are this kind.
- **Read-outs of an untrained model**: a thin API in front of a stock LLM that reads answer-token
  probabilities. These can run on an engine we already ship, but their probabilities are not calibrated.

None of the engines we ship (vLLM, SGLang, llama.cpp, Ollama) serves `/v1/systemone`.

Candidates were measured on one rented GPU, as local servers speaking the same API, against the recorded
per-item answers of the live Jev service from two sources: Kev's repository (seven suites, matched by suite
hash) and an independent third-party benchmark (five suites, rebuilt byte-identical to its published
manifests). Kev's own published numbers reproduced exactly on the serving path, which validated the setup.

## Options considered
- **Laya** (ModernBERT/mmBERT encoders, 322-421M). Rejected. Its README and a public post claim it beats Jev
  on every benchmark; on every independent suite measured it trailed Jev by 16-71 points (for example 0.648
  vs 0.857 on held-out decisions, 0.412 vs 0.892 on TypeSafe's public evals, near chance on 10-way MMLU-Pro),
  and its answers changed on 57-58% of items when options were reordered (Jev 13%). Its published wins rely
  on fine-tuning on a benchmark's own training split. It is fast (about 15 ms) and small (about 6 GB for all
  three checkpoints), which does not compensate.
- **Engine read-outs** (an SGLang sidecar; vLLM's DiffusionGemma structured mode). Deferred. The SGLang
  sidecar has no license; the vLLM mode merged after the latest release and ships only as an example server
  on an NVFP4 checkpoint. Both read uncalibrated probabilities from an untrained model. Worth revisiting when
  vLLM ships the mode in a release.
- **Open-Jev** (LoRA + head on Qwen3.5 2B/9B and Qwen3.8-27B). Not evaluated in this round. Its 27B scored
  close to Jev on its own public benchmark subset but needs about 54 GB in bf16, which conflicts with the
  smallest-GPU goal.
- **JevK5** (Qwen3.5-4B with a merged LoRA, answer-letter logit readout, Apache-2.0). **Deferred, not
  rejected.** Measured after the Kev templates were built, on JevBench's 231 public items through its own
  adapter and compared item by item with Jev's published outcomes: JevK5 bf16 200/231 (equal to Jev; hard tier
  82/111, calibration error 0.065, about 24 ms per decision), its Q8 GGUF on stock llama-server 198/231 in
  6 GB, against Kev-9B 176/231 and Kev-4B 166/231 as these templates serve them. On Kev's own suites Kev leads
  modestly; on the third-party ones JevK5 leads. It also runs on the llama.cpp build our llama-cpp image
  already ships, with a thin `/v1/systemone` layer on top. Deferred because it is days old with a single
  author, its training data is synthetic decisions in families close to JevBench's hard tier, and adopting it
  would move this onto a different image; that needs its own design review. Kev ships now.
- **Kev** (LoRA + pointer head on Qwen3.5 0.8B/4B/9B, Apache-2.0). **Chosen.** Kev-9B trails Jev by 3-8
  points on held-out suites, ties it on support-ticket routing, and is well calibrated; Kev-4B is within a few
  points of Kev-9B. Its large gap is knowledge questions (MMLU-Pro 0.52 vs 0.84), set by the base model.
  Banking77 is in Kev's training data, so its wins on the Banking77-based third-party suites are not counted.
- **Front-end: API only** (the server's `/docs` plus a notebook). Rejected as too developer-only for users
  getting to grips with a new kind of model.
- **Front-end: a `/v1/systemone` tab in `tools/model-ui`.** Deferred. It would be reusable for any future
  engine speaking the API, but `model-ui` is baked into four engine images, so changing it is a shared-tool
  change with its own review, and the pytorch image does not include it.
- **Front-end: the Kev Hugging Face Space (Gradio), adapted to call the local API.** Built and booted,
  then dropped at review in favour of the playground alone. It overlaps what the playground shows, and keeping
  it meant owning an adaptation of about 300 lines (the Space loads its own copy of the model in-process, which
  would double VRAM), plus a second Python environment and a UI whose results stream over server-sent events,
  which Cloudflare quick tunnels buffer.
- **Front-end: Kev's Next.js playground.** **Chosen.** It needs no Node install (the base image ships nvm's
  LTS), `npm ci` + `next build` take seconds, and it proxies API calls itself, so it works behind Caddy
  unchanged and adds no GPU memory. It starts only after the API has warmed its Triton kernels: the first
  request otherwise pays a JIT compile (about 25 s on a large GPU on first boot, longer on a 12 GB card).

## Decision
One provisioning manifest (`provisioning/kev/kev.yaml`) on the stock `vastai/pytorch` -mini image, and two
templates, Kev-4B and Kev-9B, each carrying its own GPU floor. The server runs with `KEV_MERGE=0`.

`KEV_MERGE=0` is what sets the floors. Kev's server loads the base model in fp32 on the GPU, merges the LoRA
adapter and then casts to bf16, so its peak is about 4 bytes per parameter (Kev-4B about 18.6 GB, Kev-9B about
34 GB). Loading directly in bf16 with the adapter unmerged fits Kev-4B in 12 GB and Kev-9B in 24 GB, measured
under per-process allocation caps, with accuracy unchanged (0.797 vs 0.799 on 656 questions for 4B; identical
for 9B) at about 30% more latency.

## Binding conditions
- Everything from outside this repo is pinned: the dated image tag, the Kev commit, hashed lock files
  installed `--no-deps`, the adapter revision in `KEV_MODEL`, the base revision from the adapter's metadata,
  and Kev's `package-lock.json`. The manifest and its two files from this repo follow `main` (reviewed
  changes reach new instances without republishing); before a merge a full commit SHA is used to test. The
  manifest refuses any other ref, and refuses a manifest URL read from a different ref.
- The image's torch must survive provisioning: the manifest asserts torch 2.8.0 after installing Kev's deps.
  Kev requires torch < 2.9, which is why the base is a torch 2.8.0 tag.
- Every service binds 127.0.0.1; Caddy is the only public listener.
- Published templates read from `main`, never from another branch.

## Consequences
- Users get an API compatible with TypeSafe's SDKs and Kev's playground on a 12 GB (4B) or 24 GB (9B) card.
  The two are the first entries in the instance portal after the portal itself.
- Pins do not move by themselves: taking a new Kev release, adapter or image is a deliberate bump (see
  `provisioning/kev/README.md`), and a stale pin is the accepted cost of not degrading silently.
- Kev serves one request at a time, so these are evaluation and development templates, not high-throughput
  deployments.

## What would reverse this
- A released engine (vLLM or SGLang) serving calibrated `/v1/systemone` decisions at comparable accuracy:
  the template would move onto an image we already ship.
- An open model closing Kev's knowledge-question gap to Jev at a similar size.
- JevK5 (or a successor) holding its JevBench lead on independent data after a design review: it would
  replace Kev and could move this onto the llama-cpp image.
- Kev adding a CPU-side merge to its server, which would remove the need for `KEV_MERGE=0`.
