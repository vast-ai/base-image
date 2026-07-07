---
name: new-image
description: Take a new Vast.ai base-image (derivative / pytorch-nested / external) from nothing to a live-GPU-tested working image + usable launch template. Use when adding a new image to this repo. The human picks the class; the agent scaffolds with the imagegen generator, fills the fenced residue to a clean lint, then builds, live-GPU tests, and iterates on real failures (the qa-fix loop, human approving each fix) until the image passes.
---

# Add a new image

The one-shot: scaffold → fill → **build → live-GPU test → iterate until it works**. Orchestrates
the deterministic generator (`tools/imagegen`) + the static linter to scaffold and fill, then
drives the image to a green live-GPU verdict, folding in the `qa-fix` diagnosis loop on each
real failure (human-gated). Read
[docs/invariants.md](../../../docs/invariants.md),
[docs/context-map.md](../../../docs/context-map.md), and
[docs/adr/0001-image-scaffolding-tooling.md](../../../docs/adr/0001-image-scaffolding-tooling.md)
first.

**Contract (non-negotiable, from ADR 0001):**
- The **human picks the class** — never infer it silently.
- Edit **only** the `>>> FILL` markers and `CHANGEME`/`CHANGEPORT` tokens. Do **not**
  touch anything outside them without surfacing it (see Escape hatch).
- **Static lint is the fast gate, not the correctness gate.** Zero lint errors means
  *structurally valid*, NOT *builds/runs*. The real `docker build` (+ smoke test) is
  the correctness gate — always say so.
- Never open a PR while `imagegen lint <name>` reports errors (including L040).

## Step 0 — Confirm the class (human decides)

Present the decision and let the human choose; if their choice looks wrong, **challenge
it once** with evidence, then defer:

| Class | When | Lives in |
|---|---|---|
| **pytorch-nested** | GPU app that needs PyTorch (image-gen, training, audio/video, transcription) — the common case | `derivatives/pytorch/derivatives/<name>/` |
| **derivative** | Needs the base image but not PyTorch (e.g. a non-torch runtime) | `derivatives/<name>/` |
| **external** | Wraps a large, trusted upstream that already ships a maintained image (vLLM, SGLang, Ollama) | `external/<name>/` |
| *provisioning-only* | Prototype/proof-of-concept; runtime install at boot | `provisioning_scripts/<name>.sh` (no dedicated image — out of scope for this skill) |

**Class-sanity check:** does the project ship its own upstream Docker image and is it
impractical to rebuild? → likely `external`. Does it import torch / need CUDA wheels? →
`pytorch-nested`, not `derivative`. If the human's pick contradicts these signals, say so.

## Step 1 — Gather inputs
- `--name` (lowercase, matches the dir), `--label` (display name), `--port` (the app's
  real bind port), and for **external** `--upstream <image:tag>`.
- The GitHub project URL + the ref/version to pin.

## Step 2 — Generate the skeleton
```bash
PYTHONPATH=tools/imagegen python3 -m imagegen.cli new \
  --class <class> --name <name> --label "<Label>" --port <port> [--upstream <image:tag>]
```
It reports `structure: valid ✓` and `skeleton: N files … NOT buildable yet`. That N is
your fill list.

## Step 3 — Study the sibling (and, for BUILD classes only, the upstream's own install)
**First know which model your class uses — `external` and the build classes are fundamentally different:**
- **`external` — you WRAP a prebuilt image; you do NOT build the app.** `FROM` the upstream's
  **published image** (their `pip install` / custom torch base is already baked in) and graft our
  overlay on top, exactly like `external/vllm/Dockerfile`: `FROM ${<NAME>_BASE}` + `COPY
  --from=base_image_source /ROOT /` + the portal / caddy / tools. From the upstream you need **only
  three things: (1) the published image tag to base on, (2) the app's launch command, (3) its
  ports.** Do **NOT** read their Dockerfile / requirements for dependencies or pins — *how they
  built their image is irrelevant to us*. Skip the dependency-resolution bullet below.
- **`pytorch-nested` / `derivative` — you BUILD the app from source into our base.** The
  dependency-resolution work below applies in full.

Sources to read before filling:
- **The closest same-class sibling** (`external/vllm/` for external; `derivatives/pytorch/derivatives/comfyui/`
  for pytorch-nested) gives the **shape**: the Dockerfile structure, the supervisor / `.conf` /
  portal wiring, the template. Match its real conventions — do not invent.
- **(BUILD classes only) The upstream's OWN install** gives the **actual dependency resolution**:
  read the app's README/wiki install guide, its `pyproject.toml` / `setup.py` / `requirements*.txt`,
  **and its own `Dockerfile`** (the authoritative install). That is where the real pins, extras, and
  prebuilt-wheel URLs live (e.g. TabbyAPI's `cu12` extra pins the exact exllamav3 / flash-attn wheel
  URLs). Then **translate** it to our pattern: install into `/venv/main` with `uv pip`, keep the base
  torch unclobbered (strip upstream torch pins; `--no-deps` on any wheel whose metadata would drag a
  different torch), all inside the drift-guard window.

This is where guessing has burned us: always diff against ground truth. For **external** that ground
truth is the **prebuilt image + the sibling's wrap** (never the upstream's build); for the build
classes it's the sibling **and** the upstream's own install.

## Step 4 — Fill only the fenced residue
Resolve every `>>> FILL` / `CHANGEME` / `CHANGEPORT`:
- **Dockerfile base (external)**: set the `>>> FILL` upstream image to the app's **prebuilt
  published tag** (`FROM ${<NAME>_BASE}` → e.g. `hiyouga/llamafactory:latest`) and leave the graft
  (`COPY --from=base_image_source /ROOT /`, portal, caddy, tools) as scaffolded — there is **no app
  install step** to write; it's already in the upstream image. The only per-app work is the base
  tag, the supervisor launch command, and the ports.
- **Dockerfile install (pytorch-nested)**: the install MUST stay **inside the existing
  RUN, between the `torch_versions_pre` and `torch_versions_post` lines** — the marker is
  already placed there; do **not** move it to a separate RUN. An install outside that
  window makes the torch-drift guard a silent no-op that the linter CANNOT catch. Keep
  the generated `[[ -n "${NAME_REF}" ]] || exit` ref-presence guard. Inside: git clone at
  the pinned ref, strip torch pins, `uv pip install`.
- **Install location — `/opt/workspace-internal/<name>`** (a1111, comfyui, sd-forge): the
  boot-sync (`36-sync-workspace.sh`) migrates that dir to `$WORKSPACE/<name>` on first
  boot (volume-backed → the app's caches/models persist across restarts). So clone into
  `/opt/workspace-internal/<name>` in the Dockerfile, and the supervisor `cd`s to
  `$WORKSPACE/<name>` (the migrated path — this is the generator's default, keep it). A
  few images (voicebox) instead keep the app in `/opt/<name>` with only a separate
  `$WORKSPACE/<name>-data` dir — that's the exception, not the rule; prefer
  workspace-internal unless the app must not be user-editable.
- **`CHANGEME` base tag**: set it to the tag the chosen **sibling currently pins** (an
  existing tag — e.g. comfyui pins a dated `vastai/pytorch:...` tag). Never invent a tag;
  a non-existent tag lints clean but fails `docker build` on the `FROM` pull. (Filling
  this token is a normal in-fence fill, NOT an escape-hatch case.)
- **supervisor script — `pty` launch driven by an `<APP>_ARGS` env** (fleet convention:
  `VLLM_ARGS`, `SGLANG_ARGS`, `LLAMA_ARGS`, `OOBABOOGA_ARGS`). Every application's launch must
  read `${<NAME_UPPER>_ARGS:-<sensible defaults>}` so a template or user can set runtime
  arguments **without touching the image** — e.g.
  `pty <cmd> ${MYAPP_ARGS:---host 127.0.0.1 --port <port>}`. Remove the `exit 1  # >>> FILL`
  stub line entirely. **The default MUST carry the explicit loopback bind** — `--host 127.0.0.1
  --port <port>`, so the launch shows which interface:port it listens on. If the app has no
  host/port flag or env override and binds only from a config file, pin host+port into that
  config at launch (don't leave the bind implicit/hidden in a baked file), and never let it
  fall back to a `0.0.0.0` default (Caddy is the sole public edge). Always loopback. Surface
  the app's real runtime flags through `<NAME_UPPER>_ARGS` (default set in the template), never
  hardcoded — so behaviour is tunable from the template.
- **Model-serving apps — provision the model at runtime, NEVER bake weights** (invariants §6;
  model this on vllm / sglang / ollama / llama-cpp). Do **not** download a model into the image.
  Expose a `<NAME_UPPER>_MODEL` env with a sensible **default** model; the supervisor waits for
  provisioning (`while [ -f /.provisioning ]; do sleep …; done`), then the app downloads that
  model to `$WORKSPACE/<name>/models` (or its own `--download-dir`) at runtime and serves it —
  and **refuses/skips if the model env is unset** (`[[ -z "${<NAME_UPPER>_MODEL:-}" ]] && { echo
  "Refusing to start — <NAME_UPPER>_MODEL not set"; exit 0; }`, like `vllm.sh`). Set the default
  `<NAME_UPPER>_MODEL` (and `<NAME_UPPER>_ARGS`) in `templates/default/template.yml`, so
  "launch the template" yields a **working model**, not an empty server. Heavier/multi-step
  setup belongs in a `provisioning_scripts/<name>.sh` (run via `PROVISIONING_SCRIPT`). Because
  the *tenant* triggers the download, the model licence stays theirs and the image stays small
  and rebuildable.
- **VRAM floor decision (the `extra_filters` `>>> FILL` block, L054 + invariants §7):** resolve
  it consciously. If the image runs **one fixed/provisioned model**, set a floor sized to it —
  `gpu_ram: {gte: <MB>}` (fits a single GPU) or `gpu_total_ram: {gte: <MB>}` (across GPUs) — so
  box selection rents a GPU that can hold it. If it's a **multi-model host** (tenant picks the
  model via `<NAME_UPPER>_MODEL`), **delete the block** — the launch template must not
  over-constrain, and QA supplies the floor at rent time (`imagegen qa --min-vram <GB>`, ADR
  0010). L054 validates the *format* of whatever you set; sizing is your judgment.
- **PORTAL_CONFIG / port wiring**: external → fill `05-<name>-env.sh` with the app's real
  bind port. **pytorch-nested / derivative** → check the sibling: if it ships a
  `ROOT/etc/vast_boot.d/05-<name>-env.sh`, add one wiring your port into PORTAL_CONFIG;
  otherwise confirm how the sibling's port reaches the portal. The `--port` is not wired
  automatically — you must place it.
- **capability yaml / agent doc**: real content (capability `readme:` = the GitHub URL; the
  agent doc = how an AI operates the app). The image-root `README.md` is developer docs.
- **recommended template + marketplace README** (`templates/default/`, ADR 0011): fill
  `template.yml` to the production recommended-template format — model it on a **vast_landing
  recommended template** (`~/vast/vast_landing/scripts/template_manager/recommended_templates/templates/`)
  or the closest base-image sibling: real `desc`, `recommended_disk_space`, ports / env /
  `PORTAL_CONFIG`. **Keep the scaffold's `compute_cap` floor** (L050) even if the exemplar uses
  a different `extra_filters` shape. Fill `templates/default/README.md` (the marketplace
  listing `create.py` injects) to the recommended structure — keep the **`<<LAUNCH_LINK>>`
  placeholder** (never a hardcoded `cloud.vast.ai/?ref_id=` link — **L052 fails**) and a real
  Licenses section.

**Escape hatch:** if a correct change requires touching **structure the generator did not
scaffold** — adding a build stage, changing the CI job shape, changing the class or the
base-image repo, or any edit outside a `>>> FILL` / `CHANGE*` token — **stop and surface
it to the human**. (Resolving the scaffolded tokens themselves is not an escape-hatch
case.)

## Step 5 — Lint to zero
```bash
PYTHONPATH=tools/imagegen python3 -m imagegen.cli lint <name>
```
Loop until 0 errors. All `L040` (unfilled markers) must clear. If a structural check
(L001–L030) fails, fix the cause; do not work around the linter.

## Step 6 — Build, test, and iterate to a working image
Lint green is NOT done — it means "ready to build," never "runs" (ADR 0001). Now DRIVE the
image to a live-GPU pass, iterating on real failures — the one-shot's core. It's
**human-gated**: you approve each fix diff (this is not the unattended `--autofix`, gated
behind ADR 0009 cond 9). From here the editable surface widens from Step 4's FILL-only to the
**`qa-fix` closed surface** — the image's own `Dockerfile` / `ROOT/opt/supervisor-scripts/*.sh`
/ `templates/*.yml` — because you're now fixing real runtime failures, not filling a skeleton.

**Running `imagegen` here:** `new`/`lint` are pure-stdlib (any `python3`), but
`build`/`qa`/`publish` shell `tools/template_manager` (its venv + the account `.env`) and
there is **no `imagegen` on PATH**. Invoke those as
`PYTHONPATH=tools/imagegen .venv/bin/python -m imagegen.cli <build|qa|publish|qa-teardown> …`
(one-time venv + creds setup: `tools/imagegen/README.md`).

1. **Build + push** — `build <name> [--ref <upstream-ref>] --tag <ns>/<name>:<tag> --push`
   (pytorch-nested/derivative: `--ref` sets the `<NAME>_REF` build-arg; external: omit it;
   `--push` auto-creates the staging repo public). A `docker build` failure is a fill bug —
   fix it and rebuild; don't QA a broken build.
2. **Live-GPU test** — `qa <name> --tag <ns>/<name>:<tag>` boots the `templates/default`
   launch template at the staging image and runs the baked functional test.
   - **PASS** → the image works AND its launch template is validated (ADR 0010) → Step 7.
   - **FAIL** → it HOLDS the box + writes `.qa/bundle.json` → step 3.
3. **Diagnose + fix on the held box — follow the `qa-fix` skill**; its procedure *is* this
   step (read the bundle, SSH in, root-cause against the upstream's OWN install, verify the
   fix ON the box, bake it into the closed surface — you approve the diff).
4. **Rebuild + re-test** — `build <name> --push` then `qa <name>`. Loop 1→4 until green, but
   **bound it**: same failure signature twice → the bake didn't reproduce the live fix, stop
   and surface; `upstream-broken` (nothing in-image resolves it live) → STOP with evidence; a
   fix needing anything **outside the image's own files** → escape hatch (may be a
   Bug→Invariant for the linter). Tear down on stop: `qa-teardown <name>`.

A **green rebuilt** verdict is the certification (live-green was only a hypothesis). The
deliverable is real: a working image + the usable launch template it was tested through.

## Step 7 — Hand off
Once green, prepare the change / open a PR referencing the tracking ticket (e.g. CON-####). The `build-<name>.yml` workflow is
scaffolded as the **full 6-job QA-gated pipeline** (preflight → build → **qa** →
merge-manifests → collect-tags → notify) with the DockerHub **secret-refs, the `qa` job
calling `qa-gate.yml` (promotion gated on it), the `production` approval gate, and notify
(with the gated-pass headline) already wired**. You fill only the `CHANGEME`/`>>> FILL`
bits: the preflight `check-*-release` action, the base-image matrix, the tag derivation, a
staggered schedule offset, and — in the **qa** job — the cuda/py matrix, the staging tag,
and `log_paths`. CI job-shape is **not linted**, so still review against a sibling.

The generator scaffolds **one template** — `templates/default/` (ADR 0010/0011): the
production-ready **recommended** template (`template.yml` + a rich `README.md`) that the QA
gate also boots, so "QA passed" means "the template users launch passed." It carries the
recommended production fields (`image: vastai/<name>`, `tag: "@vastai-automatic-tag"`,
`href`/`repo`, `desc`, `recommended_disk_space`, `private: false`, a `compute_cap` floor for
L050), and its `README.md` uses the `<<LAUNCH_LINK>>` placeholder (L052). Fill its launch spec
(ports / env / `PORTAL_CONFIG`, wiring the app's real interface:port into the portal). The gate
overrides `image`/`tag` to the staging image at publish; the functional test is the image's
baked `ROOT/opt/instance-tools/tests/<name>.d/`, **not** a template field. If this image
genuinely **cannot** be functionally tested, removing the `qa` job (and dropping `qa` from the
`needs:` of merge-manifests/notify) is an **escape-hatch** — surface it, don't silently strip it.

**Publish a live dogfood template (ADR 0011):** once QA is green, run `imagegen publish <name>`
— it publishes a **private, staging-pointed, idempotent** copy of `templates/default` (named
from the template's `name`, delete-prior so runs don't accrete duplicates) on the account in
`.env`, and prints a launch link to dogfood the freshly-built image immediately. This is **not**
the production publish: the public, prod-image recommended template is published through
**vast_landing** at promotion (base-image `templates/default` is the production-ready source a
human promotes unchanged — there is no automated sync).

**Docker Hub repos — the staging repo must be PUBLIC (QA pulls it anonymously):**
- `imagegen build <name> --push` **auto-creates the staging repo public** if it's missing
  (using your `docker login` creds) — so for a local run you usually don't create it by
  hand. A bare `docker push` alone would auto-create it **private**, which the rented test
  GPU can't pull. If the auto-create can't authenticate (no inline docker creds), create
  `${DOCKERHUB_NAMESPACE_STAGING}/<name>` public yourself. (Same repo name as the eventual
  prod repo.)
- The **prod** repo (`${DOCKERHUB_NAMESPACE}/<name>`, same name) is created at
  **promotion**, which is already behind the workflow's `production` approval — so it
  need not exist yet. **QA never needs prod** (it tests the staging image), so a new
  image builds and QA's fine before any prod repo exists.
- Namespaces are single-sourced as the `DOCKERHUB_NAMESPACE_STAGING` / `DOCKERHUB_NAMESPACE`
  secrets. In any **committed** file (workflow, docs, scaffold) reference the secret —
  `${{ secrets.DOCKERHUB_NAMESPACE_STAGING }}` — never a literal account name. This is
  about keeping the config single-sourced, not secrecy (a namespace is a public
  identifier). **L041 fails the lint** if a new image's committed files hardcode it.
