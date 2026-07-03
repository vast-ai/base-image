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

## Step 3 — Study a real sibling FIRST
Before filling, read the closest **same-class** existing image as an exemplar (e.g. a
pytorch-nested app like `derivatives/pytorch/derivatives/comfyui/`, or `external/vllm/`).
Match its real conventions — do not invent. (This is where guessing has burned us:
always diff against ground truth.)

## Step 4 — Fill only the fenced residue
Resolve every `>>> FILL` / `CHANGEME` / `CHANGEPORT`:
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
- **supervisor script**: the real launch (use `pty`, like the sibling); remove the
  `exit 1  # >>> FILL` stub line entirely. **Make the bind explicit** — the launch must
  show which interface:port it listens on: pass the app's `--host 127.0.0.1 --port <port>`
  flags. If the app has no host/port flag or env override and binds only from a config
  file, pin host+port into that config at launch (don't leave the bind implicit/hidden in
  a baked file), and never let it fall back to a `0.0.0.0` default (Caddy is the sole
  public edge). Always loopback.
- **PORTAL_CONFIG / port wiring**: external → fill `05-<name>-env.sh` with the app's real
  bind port. **pytorch-nested / derivative** → check the sibling: if it ships a
  `ROOT/etc/vast_boot.d/05-<name>-env.sh`, add one wiring your port into PORTAL_CONFIG;
  otherwise confirm how the sibling's port reaches the portal. The `--port` is not wired
  automatically — you must place it.
- **capability yaml / agent doc / READMEs**: real content (capability `readme:` = the
  GitHub URL; README.md = dev docs; README.template.md = marketplace listing — distinct).

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

**Running `imagegen` here:** `new`/`lint` are pure-stdlib (any `python3`), but `build`/`qa`
shell `tools/template_manager` (its venv + the QA-account `.env`) and there is **no `imagegen`
on PATH**. Invoke those as
`PYTHONPATH=tools/imagegen .venv/bin/python -m imagegen.cli <build|qa|qa-teardown> …`
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

The generator scaffolds **one template** — `templates/default/template.yml` (ADR 0010): the
public, user-facing launch template (`private: false`, `readme_visible: true`, `image:
vastai/<name>`, a placeholder `compute_cap` floor for L050) **that the QA gate also boots**,
so "QA passed" means "the template users launch passed." Fill its launch spec (ports / env /
`PORTAL_CONFIG`, wiring the app's real interface:port into the portal). The gate overrides
`image`/`tag` to the staging image at publish; the functional test is the image's baked
`ROOT/opt/instance-tools/tests/<name>.d/`, **not** a template field. If this image genuinely
**cannot** be functionally tested, removing the `qa` job (and dropping `qa` from the `needs:`
of merge-manifests/notify) is an **escape-hatch** — surface it, don't silently strip it.

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
