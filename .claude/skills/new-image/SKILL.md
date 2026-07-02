---
name: new-image
description: Scaffold a new Vast.ai base-image (derivative / pytorch-nested / external) for a GitHub project, using the imagegen generator + linter. Use when adding a new image to this repo. The human picks the class; the agent fills only the fenced residue and must reach a clean lint before a PR.
---

# Add a new image

Orchestrates the deterministic generator (`tools/imagegen`) and the static linter to
scaffold a new image, then fills only the judgment residue. Read
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
- **`CHANGEME` base tag**: set it to the tag the chosen **sibling currently pins** (an
  existing tag — e.g. comfyui pins a dated `vastai/pytorch:...` tag). Never invent a tag;
  a non-existent tag lints clean but fails `docker build` on the `FROM` pull. (Filling
  this token is a normal in-fence fill, NOT an escape-hatch case.)
- **supervisor script**: the real launch (use `pty`, like the sibling); ensure it binds
  the `--port` you supplied; remove the `exit 1  # >>> FILL` stub line entirely.
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

## Step 6 — Hand off honestly
State plainly: lint is green = structurally conformant; the image is **not** verified
until the real CI `docker build` + smoke test pass. Then prepare the change / open a PR
referencing the tracking ticket (e.g. CON-####). The `build-<name>.yml` workflow is
scaffolded as the **full 6-job QA-gated pipeline** (preflight → build → **qa** →
merge-manifests → collect-tags → notify) with the DockerHub **secret-refs, the `qa` job
calling `qa-gate.yml` (promotion gated on it), the `production` approval gate, and notify
(with the gated-pass headline) already wired**. You fill only the `CHANGEME`/`>>> FILL`
bits: the preflight `check-*-release` action, the base-image matrix, the tag derivation, a
staggered schedule offset, and — in the **qa** job — the cuda/py matrix, the staging tag,
and `log_paths`. CI job-shape is **not linted**, so still review against a sibling.

The generator also scaffolds a **QA template** at `templates/<name>-qa/template.yml`
(private, with a placeholder `compute_cap` floor) — fill its launch spec + functional
test (the gate boots this image on a real GPU and runs it) modelled on a sibling
`*-qa/template.yml`. If this image genuinely **cannot** be functionally tested, removing
the `qa` job (and dropping `qa` from the `needs:` of merge-manifests/notify) is an
**escape-hatch** case — surface it to the human, don't silently strip it.

**Docker Hub repos — create staging *before* the build:**
- Create the **staging** repo `${DOCKERHUB_NAMESPACE_STAGING}/<name>` and set it
  **public** *before the first build*. The build pushes per-arch tags there, and if the
  image is QA-gated the rented test GPU pulls that staging image **anonymously** — a
  private staging repo fails the pull. (Same repo name as the eventual prod repo.)
- The **prod** repo (`${DOCKERHUB_NAMESPACE}/<name>`, same name) is created at
  **promotion**, which is already behind the workflow's `production` approval — so it
  need not exist yet. **QA never needs prod** (it tests the staging image), so a new
  image builds and QA's fine before any prod repo exists.
- Namespaces are single-sourced as the `DOCKERHUB_NAMESPACE_STAGING` / `DOCKERHUB_NAMESPACE`
  secrets. In any **committed** file (workflow, docs, scaffold) reference the secret —
  `${{ secrets.DOCKERHUB_NAMESPACE_STAGING }}` — never a literal account name. This is
  about keeping the config single-sourced, not secrecy (a namespace is a public
  identifier). **L041 fails the lint** if a new image's committed files hardcode it.
