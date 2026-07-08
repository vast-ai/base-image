# Invariants

Rules the image family actually relies on, **verified against the real files** (not
transcribed from docs). This is the spec a static linter should encode (see
[ADR 0001](adr/0001-image-scaffolding-tooling.md)). Where reality diverges from
`CONTRIBUTING.md` / `.github/AGENTS.md`, it is flagged — **reality wins here.**

Classes: **derivative** (`FROM vastai/base-image`), **pytorch-nested**
(`FROM vastai/pytorch`, under `derivatives/pytorch/derivatives/`), **external**
(`external/*`, multi-stage wrapping an upstream image).

> ⚠️ **Key finding for the linter:** the pattern is *less* uniform than the docs
> claim. Some headline "rules" cannot reach a clean baseline and **must not be
> gated** (see §3). Most importantly, **`external port == internal + 10000` is
> NOT a real invariant** — this directly affects ADR 0001's plan.

---

## 1. Hard invariants — safe to GATE (clean baseline reachable)

All verified clean across existing images.

- **3 LABELs.** Exactly three top-level `LABEL` lines, keys:
  `org.opencontainers.image.source`, `org.opencontainers.image.description`
  (value ends `suitable for Vast.ai.`), `maintainer="Vast.ai Inc <contact@vast.ai>"`.
- **`env-hash` trailer.** Final build instruction is `env-hash > /.env_hash`.
  *(External: it's the last `RUN`, before only `ENTRYPOINT`/`CMD` — not literally
  the last line.)*
- **`COPY ./ROOT /`** present exactly once. *(External also has
  `COPY --from=base_image_source /ROOT /`.)*
- **External graft block** (`external` only): the fixed env block → 4 `COPY`s
  (incl. `convert-non-vast-image.sh`) → the convert `RUN` → `COPY ./ROOT /` →
  `ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]` + `CMD []`.
- **No surviving `--torch-backend auto`.** The only `auto` occurrences are `sed`
  rewrites that *replace* it with a concrete backend. Flag `auto` only when it's
  the *argument of an install command*.
- **`uv pip` only**, never bare `pip install` (pytorch-nested; spot-check externals).
- **Supervisor util source ORDER.** When utils are sourced, they appear as a
  *subsequence* of: `logging.sh → cleanup_generic.sh → environment.sh →
  exit_serverless.sh → exit_portal.sh`. Zero inversions exist. Match on path
  (`logging.sh` is sometimes called with an argument).
- **conf.d ↔ script ↔ program-name triple** (STRONGEST invariant, 60/60 clean):
  every `etc/supervisor/conf.d/*.conf` has `environment=PROC_NAME="%(program_name)s"`,
  `command=/opt/supervisor-scripts/<x>.sh`, and `[program:NAME]` where `NAME` ==
  conf filename stem. Bonus checkable: the `command=` target script exists on disk.
- **External `05-<name>-env.sh`** present setting `PORTAL_CONFIG` (externals + a
  few derivatives).
- **PORTAL_CONFIG anchors.** First entry always
  `localhost:1111:11111:/:Instance Portal`; a Jupyter entry present.
- **One `build-<name>.yml` per image.**
- **External build passes `--build-context base_image_source=.`** (all 5). This is
  what makes the otherwise-undeclared `base_image_source` stage resolve.

> Note: "one `build-<name>.yml` per image" is **not** universal — 6 images
> (fooocus, kohya_ss, oobabooga, swarmui, tensorflow, UnrealPixelStreaming) build
> via shared/other workflows. The linter treats workflow presence as a WARN (L030),
> not a gate.

## 2. Conditional invariants — GATE per-class, with exceptions encoded

- **FROM matches class** — EXCEPT: `aio-studio` builds on a custom
  `robatvastai/aio-studio:base-*` (not `vastai/pytorch`); `external/openwebui`'s
  upstream stage has **no `AS` alias** (others use `*_build`). The
  `vast_base_image`-first / upstream-second ORDER *is* hard across all externals.
- **torch-drift guard** (pytorch-nested) — present 16/17. EXEMPT: `aio-studio`
  (per-app venvs). ⚠️ The doc describes the **stale torch-only** form; reality
  checks the 4-package ecosystem (`torch|torchvision|torchaudio|torchcodec`).
- **strip upstream torch pins** (`sed` before install) — strong convention, not
  universal; gate only "if requirements installed, a torch-strip precedes it."
- **CI job set** — app images: `{preflight, build, merge-manifests, collect-tags,
  notify}` (allow `resolve-refs` variant; allow drop of `merge-manifests` for
  single-arch, e.g. voicebox). EXEMPT: `base-image`/`pytorch` (bespoke pipelines),
  `aio-studio-base` (2-job). ⚠️ Docs say 4-job and **omit `merge-manifests`** —
  wrong; 5-job dominates (16 workflows).
- **`MATRIX_ID` / built-tag** — `md5sum | cut -c1-8`; artifact `built-tag-*`.
  Strong convention in app workflows.

## 3. NOT invariants — DO NOT GATE (doc claims false against reality)

- ❌ **`external port == internal + 10000`.** Widely violated: Jupyter terminal
  (delta 0), `ollama` (reversed: `21434:11434`) vs `openwebui` (`11434:21434`) —
  the two even disagree on the *same* service; `aio-studio` pervasively (columns
  appear transposed). **This kills a binding assumption in ADR 0001.** At most a
  *soft warning* excluding known-exempt labels; possibly flag "likely transposed
  columns."
- ❌ **Fixed util SET** ("must source all 4"). The set varies legitimately;
  only the order is invariant. *(And there are 6 utils, not 4 —
  `exit_serverless.sh`, `pty.sh` are undocumented.)*
- ❌ **Uniform cron** `'0 0,12 * * *'`. Deliberately staggered across images.
- ❌ **Required `DEFAULT_MULTI_ARCH` / `RELEASE_AGE_THRESHOLD`.** First exists in
  one workflow; the latter is class-dependent (`COMMIT_AGE_THRESHOLD` for git-ref
  images) and absent in several. Only `DEFAULT_DOCKERHUB_REPO` is near-hard.
- ❌ **`set -euo pipefail` in every RUN.** Only the primary install RUN reliably
  has it.
- ❌ **The docs' specific `vast_boot.d` script list.** Numbering scheme is a rule
  (`^[0-9]{2}-.*\.sh$`); the exact list is mid-migration and already wrong in docs.

## 4. Real but NOT statically checkable (linter blind spots → need the build)

These are why ADR 0001 condition #1 holds: **static lint is the fast gate, the
real `docker build` + smoke test is the correctness gate.**

- torch ecosystem *actually* unchanged after install (guard presence ≠ success).
- CPU smoke-tests pass (presence checkable; success runtime-only).
- PORTAL_CONFIG ports match the port the app actually binds.
- Tag commit-hash-vs-version date suffix (depends on the runtime-resolved ref).
- `base_image_source` build-context *content*.
- single shared `/venv/main` assumption (false for aio-studio by design).

**Feasible future cross-file static check:** every `exit_portal.sh "<Label>"` in
an image should have a matching `:<Label>` entry in that image's `PORTAL_CONFIG`
(catches typos). Not currently enforced.

## 5. Docs needing correction before they can be trusted

`CONTRIBUTING.md` / `.github/AGENTS.md` are stale on: the torch guard form (§2),
the boot-sequence list (§3), the 4-job pipeline + missing `merge-manifests` (§2),
the undeclared `base_image_source` stage (§1), the missing utils
`exit_serverless.sh`/`pty.sh` (§1), the port +10000 claim (§3), and uniform cron (§3).

## 6. Invariants codified from review

The no-baked-weights policy is now **gated (L053)**, the public-ADR-secret policy is
**gated (L060)**, and the no-internal-ticket-id policy is **gated (L061)**. The copyleft
policy remains review-only for now (statically checkable; should become a lint rule with
the ADR-binding + `RULES`-catalog pattern of §1).

### No baked model weights — **GATED (L053)**

Model weights must NOT be downloaded or baked into the image; they arrive at
runtime via provisioning or the app's own on-start download (see the runtime
conventions in §7). Rationale: keeps images small and rebuildable, and — because
the *tenant* triggers the download — the weight licence (non-commercial / gated /
territory-restricted) is the tenant's to honour, not something the image distributes.

**L053** enforces it, instruction-aware (operates on the parsed RUN code, so a
*commented* example download does not fire). Detected inside a Dockerfile `RUN`:
`hf download` / `huggingface-cli download` / `hf_hub_download(` / `snapshot_download(`,
and a `wget`/`curl` of `*.safetensors|*.gguf|*.ckpt|*.pth|*.onnx`. `*.bin` is
deliberately **excluded** (too many non-model `.bin` files → false positives). Scope:
model weights only — not small non-model assets (tokenizer/config files, a UI's
bundled icons).

**Exemption (dated, tracked):** `comfyui` bakes one small default SD-1.5 checkpoint
for the out-of-box / QA first-run (a §6-style deviation, tracked for migration to
runtime provisioning — see the `EXCEPTIONS` entry). It is the only current exemption;
new images must provision, not bake.

### No credential-shaped secret in a public ADR — **GATED (L060)**

`base-image` is public, and `docs/adr/**` is world-readable. An ADR records the
decision + rationale + rejected alternatives; a credential, an exploit-map, or
account/business specifics do not belong in it — they live in the linked Jira issue
(ADR 0012). **L060** (`check_adr_secrets`, a repo-level check — not per-image) scans
`docs/adr/*.md` for credential *shapes*: private-key blocks, AWS access-key ids,
GitHub/Slack tokens, JWTs, and a secret-named field assigned a literal high-entropy
value. Prose mentions of "token"/"key"/"secret" and env-var references (e.g.
`VAST_API_KEY`) do **not** fire — only a credential-shaped value does. The
*exploit-map* half of the ADR-0012 guardrail (which token, which transport, which
soft endpoint) is not machine-detectable and stays review-enforced. Precedent for
the excision: ADR 0005 condition 8, moved to the internal issue.

### No internal tracker ticket id in a public file — **GATED (L061)**

`base-image` is public. An internal Jira ticket id (a Vast-internal project key such
as `CON-`/`HOST-`/`CLN-`-####) must not appear in any repo file — it leaks the
tracker's structure and is a dangling reference to a private system for external
readers. The linkage runs one way: the internal issue references the public
ADR/commit, never the reverse. **L061** (`check_internal_ticket_ids`, repo-level)
scans the working tree (text files + Dockerfiles, incl. the first-party `external/`
wrappers) for an explicit internal-prefix set — extend `_INTERNAL_TRACKERS` as new
internal projects appear. Precedent: the CON-/HOST-/CLN- references scrubbed from
docs, workflows, templates, and tooling when this rule landed (ADR 0012).

### Copyleft licence compliance (proposed)

An image that ships GPL-/AGPL-licensed code must (a) convey the licence **text** in
the image (a LICENSE at a known path, vendored to `/licenses/` if the package does
not carry one), and (b) when the Dockerfile patches the copyleft upstream (a
`sed -i` / `patch` / `git apply` against a cloned or installed copyleft source),
carry a **modification notice** in `LICENSES.md` — GPL §5 / AGPL §4 + §5a.
Corresponding source is this public repo; AGPL §13 (network source offer to users)
stays the tenant-operator's obligation, not the image's.

Because "is this upstream copyleft?" is not reliably inferable, the check is
**declarative**: `LICENSES.md` declares the licence, and the rule verifies that for
each copyleft entry (a) the stated in-image licence path resolves and (b) a
`Modifications:` note exists whenever the Dockerfile patches that app's tree.
Applies to GPL-3.0 too (e.g. ComfyUI), not only AGPL. Reference implementation of
the obligations themselves: the `fix/agpl-license-compliance` change (unsloth-studio,
aio-studio, a1111, sd-forge, oobabooga).

## 7. Application runtime conventions (how apps are launched & fed models)

These govern how an application's supervisor script launches the app and how a model
reaches it. Verified across the LLM-serving fleet (vllm, sglang, ollama, llama-cpp,
oobabooga). The `new-image` skill + generator encode them.

- **`<APP>_ARGS` — runtime args from the template, not baked into code.** The primary
  app-server launch reads `${<NAME_UPPER>_ARGS:-<sensible defaults incl. the explicit
  loopback bind>}`, so a template or user tunes runtime flags without editing the image
  (`VLLM_ARGS`, `SGLANG_ARGS`, `LLAMA_ARGS`, `OOBABOOGA_ARGS`, …). The default **must**
  carry `--host 127.0.0.1 --port <port>` (Caddy is the sole public edge; never `0.0.0.0`).
  **Not statically gated** — it is *not* universal by design: config-file apps (invokeai,
  fluxgym, … pin via a config file, not ARGS) and infra/helper services (desktop stack,
  `model-ui`, `api-wrapper`, `ray`) legitimately have no `<APP>_ARGS`, and there is no
  reliable static way to distinguish "primary launch that should have ARGS" from those. It
  is enforced by the skill + scaffold, not the linter.

- **`<APP>_MODEL` + provision the model at runtime (never bake — see §6 / L053).** A
  model-serving app names a default model via an `<APP>_MODEL` env set in
  `templates/default/template.yml` (`VLLM_MODEL: Qwen/Qwen3-8B-FP8`, `SGLANG_MODEL`,
  `OLLAMA_MODEL`, `LLAMA_MODEL`). The supervisor waits for provisioning
  (`while [ -f /.provisioning ]; do …`), then the app downloads that model to
  `$WORKSPACE/<name>/models` (or its own `--download-dir`) at runtime and serves it,
  **refusing/skipping if the model env is unset** (`vllm.sh`: *"Refusing to start —
  VLLM_MODEL not set"*). Heavier/multi-step setup lives in a `provisioning_scripts/<name>.sh`
  (run via `PROVISIONING_SCRIPT`). So "launch the template" yields a working model, and no
  weights ship in the image.
- **VRAM floor sized to the model — `gpu_ram` / `gpu_total_ram` (validity GATED, L054).** A
  template's `extra_filters` should carry a VRAM floor sized to the model the image actually
  runs, so box selection rents a GPU that can hold it — `gpu_ram: {gte: <MB>}` (must fit a
  single GPU) or `gpu_total_ram: {gte: <MB>}` (summed across GPUs). **Boundary:** a *single*
  fixed/provisioned-model image SHOULD set it; a **multi-model host** (the user picks the model
  via `<APP>_MODEL`) leaves it **unset** on the launch template, and the live-GPU gate supplies
  a floor at rent time (`imagegen qa --min-vram <GB>` → injected `gpu_total_ram`, ADR 0010) so
  the *test* model fits without over-constraining the launch template. Presence + the value are
  judgment (the linter can't know a model's footprint); **L054 gates only FORMAT** — a VRAM
  filter, if set, must use a valid key with a numeric floor (a misspelled key or a key-only
  floor lints falsely clean but selects nothing).
- **External images set `TCLLIBPATH` (GATED, L055).** An external image `FROM`s the upstream's
  prebuilt image, so it does NOT inherit the base image's ENV. It must set
  `ENV TCLLIBPATH=/usr/lib/tcltk/default` itself, or the base pty helper's `unbuffer` (Tcl/Expect)
  fails early in boot and the launch cascade dies — the LLaMA-Factory scaffold shipped without it
  and died on a live box (no supervisord, `can't find package Expect`). The `/opt/sys-venv/shim`
  PATH entry most externals also carry is **NOT gated**: `vast_boot.d/10-prep-env.sh` adds it at
  runtime, so `vllm-omni` omits it from the Dockerfile and works fine. Root cause was a generator
  bug (`_DF_EXTERNAL` set neither); fixed + gated.
