# ADR 0004 — oobabooga: accel-wheel/torch reconciliation, ADR-0002 convention backfill, and CI

- **Status:** Accepted (conditional — see Binding conditions). **Amended 2026-06-25
  — see Amendment below.**
- **Date:** 2026-06-25
- **Decision owner:** Rob Ballantyne
- **Process:** idea brief → red-team gate → 3 blind architects → 3-lens blind panel
  (feasibility / maintainability / risk) → synthesis → final red-team gate on the
  plan (which empirically tested the load-bearing assumption).

## Amendment (2026-06-25) — track latest upstream, do not pin

The original synthesis pinned `OOBABOOGA_REF` to a release tag (`v4.9`) and shipped a
static, owned `ROOT/opt/accel-wheels.txt` manifest validated against that tag (original
condition 5: "REF and manifest move together"). **This was reversed**: pinning broke the
house automation — every other derivative's CI resolves the latest upstream HEAD and
rebuilds, and a pinned ref silently stops tracking upstream. Per the decision owner, the
correct posture is: **CI resolves the latest upstream ref (HEAD of `main`, like the
sibling workflows) and builds from it, accepting the risk that a future ref breaks the
build** — the live-GPU QA gate (ADR 0005) is the runtime net being built for exactly this.

Concretely, superseding the affected parts below:
- **Install:** the static `accel-wheels.txt` manifest is **removed**. The Dockerfile now
  derives the accel wheels from the *resolved ref's* `requirements/full/requirements.txt`
  at build time (REF-agnostic): rewrite the cp313→cp312 ABI tag + marker, strip the base
  torch ecosystem, split the externally-hosted accel wheels out **by stable host/org
  token** (`turboderp-org/exllamav3`, `mjun0812/flash-attention-prebuild-wheels`,
  `oobabooga/llama-cpp-binaries`, `download.pytorch.org/...xformers-`) and install them
  `--no-deps`, then install the rest under a torch-ecosystem constraints floor. `--no-deps`
  + the drift-guard + the hard ABI import gate are retained unchanged.
- **CI (was condition-5 pinning):** `OOBABOOGA_REF` resolves to **HEAD of upstream `main`**
  via the GitHub API with a commit-age threshold (scheduled runs skip stale upstreams) and
  a manual override — matching `build-a1111.yml` / `build-fluxgym.yml`.
- **Linter:** the manifest-coupled **`L053`** rule is **removed** (it keyed on the now-gone
  `accel-wheels.txt`). The pre-existing **`L020`** drift-guard remains the codified
  invariant that the base torch cannot be silently downgraded.

Below, references to "the owned manifest" / "pinned tag" / "condition 5" / "L053" are
retained as the historical record but are superseded by this amendment.

## Context

Bring the `oobabooga` (text-generation-webui) derivative
(`derivatives/pytorch/derivatives/oobabooga/`) up to current conventions and give
it a dedicated build pipeline. Three coupled needs, surfaced as two `imagegen lint`
WARNs (`L052` no baked `PORTAL_CONFIG`; `L030` no `build-oobabooga.yml`) plus a
"verify the base is appropriate" ask.

Verified reality that shaped the decision:

- **The build is latently RED and has been for some time** — there is no CI, so it
  was never caught. oobabooga upstream `requirements/full/requirements.txt`
  (release `v4.9`) selects, on Linux x86_64, accel wheels whose **wheel METADATA
  hard-pins `torch==2.9.0` and `xformers==0.0.33`** (exllamav3 0.0.34; flash_attn
  2.8.3 is `+cu128torch2.9`). The pytorch base family ships **torch 2.9.1 only** —
  2.9.0 was *deliberately superseded* (`build-pytorch.yml:93` "patch versions
  maintain ABI compat — 2.9.0 superseded"); **no 2.9.0 `-mini` base exists**, and a
  py313 base is also 2.9.1. The Dockerfile hard-pins torch to the inherited 2.9.1
  with a pre/post drift-guard that fails the build on any torch change. So
  exllamav3's `torch==2.9.0` collides with 2.9.1 → resolver error or torch
  downgrade → drift-guard trips.
- The accel wheels (exllamav3, flash_attn, xformers, llama_cpp_binaries) are
  **x86_64-only** → the image is **amd64-only**; arm64 has no backend wheels.
- The app binds **loopback** by default: `oobabooga.sh` launches
  `python server.py --listen-port 17860 --api --api-port 15000` **without
  `--listen`**, and upstream binds `127.0.0.1` for both the UI (`server.py`) and the
  API (`modules/api/script.py` binds `0.0.0.0` only when `--listen` is set). The
  baked default is therefore safe.
- [ADR 0002](0002-portal-config-and-expose-conventions.md) governs the convention
  half: bake a default `PORTAL_CONFIG` + `EXPOSE` the Caddy-front ports. oobabooga is
  one of "the 24" awaiting backfill (ADR 0002 migration step 3). **Binding
  condition 1 of ADR 0002 requires a runtime `ss -ltnp` 0.0.0.0-bind smoke gate for
  any EXPOSE-ing image** and states that deferring it "voids the decision." This ADR
  addresses that head-on (see Binding conditions / partial amendment of ADR 0002).

The owner's two product/governance calls: **install exllamav3 anyway** (2.9.0 vs
2.9.1 is a patch diff and PyTorch keeps patch ABI compat — the base family's own
"superseded" comment asserts this); and, because **no GPU-class CI runners exist
yet**, **defer the runtime bind gate to a downstream QA gate** rather than wire it
into CI now. The UI **and** the API both get authed Caddy fronts.

## Options considered

The install-strategy fork drew three blind architect designs, scored by a 3-lens
blind panel. Scores (1–10): feasibility / maintainability / risk.

- **Option A — minimal `sed` repair (Alpha; 7 / 3 / 5).** Keep the existing
  `cp313→cp312` sed; drop the index-xformers; pull the exllamav3 line out and
  install it `--no-deps`; one-line `import exllamav3` check. **Rejected:** the
  maintainability lens showed the sed "works today *only by coincidence* of
  upstream's wheel-naming" and the live `audioop-lts ... python_version >= "3.13"`
  line (uses `>=`, not `==`) already proves the pattern-match silently rots; the
  risk lens showed its one-line `import exllamav3` may not catch the ABI break at
  all (exllamav3 lazy/JIT-loads its CUDA ext — oobabooga #4554), and even when it
  fires it drags in the pure-Python closure and fails RED for non-ABI reasons.
- **Option C — owned manifest + uv constraints (Beta; 9 / 8 / 2).** Stop
  transforming upstream's file: a derivative-owned `accel-wheels.txt` of explicit
  cp312 URLs installed `--no-deps`, a uv `-c constraints.txt` pinning
  `torch==${PYTORCH_VERSION}` + `xformers==0.0.33`, requirements stripped by stable
  token, **cp313 sed dropped entirely**. Won feasibility (the constraints file makes
  the torch downgrade *structurally impossible*, not merely line-removed) and
  maintainability (deletes the whole silent-rot class). **Risk lens scored it 2 and
  said "block":** it never tests that the compiled extension actually *loads*
  against 2.9.1 — a confident green over an untested ABI assumption that fails on a
  paying user's GPU.
- **Option G — import-proof gate + safe-degrade (Gamma; 4 / 4 / 7).** `--no-deps`
  install + a build-time import of the *compiled submodule* (the only real ABI
  proof), but on failure it **uninstalls the package and ships GREEN-but-degraded**
  with a `/.abi_degraded` marker and a CI `::warning::`. Won the risk lens (real
  ABI proof) but **feasibility called the degrade a "correctness bug"** (ships green
  while masking the exact failure the task exists to fix) and maintainability called
  green-but-degraded "silent capability loss behind an ignorable warning."

The panel was complementary, not contradictory: Option C's install mechanism and
Option G's *verification* each fix the other's sole weakness. Both other judges were
unanimous against G's **degrade** path specifically.

Also rejected: a torch-2.9.0 base (none exists; fights the family's superseded
decision); dropping exllamav3 outright (gives up a headline loader without testing
the — empirically valid — compatibility; retained only as the explicit fail-RED
runbook escape, below).

## Decision

**Adopt Option C's install mechanism, grafted with Option G's verification as a
HARD gate (fail RED, no degrade).**

1. **Install (Dockerfile):**
   - Derivative-owned `ROOT/opt/accel-wheels.txt` naming the exact validated
     Linux/x86_64 **cp312** wheel URLs (exllamav3 0.0.34, flash_attn 2.8.3,
     llama_cpp_binaries + ik_llama_cpp_binaries 0.136.0, xformers 0.0.33), installed
     **`uv pip install --no-deps`** (amd64-gated). `--no-deps` is the load-bearing
     mechanism: it severs exllamav3's `torch==2.9.0` / `xformers==0.0.33` METADATA
     pins so the base's torch 2.9.1 is never downgraded.
   - A uv **`-c constraints.txt`** pinning `torch==${PYTORCH_VERSION}`,
     torch{vision,audio,codec}, and `xformers==0.0.33`, applied to the remaining
     upstream requirements install — a declarative "torch must not move" floor.
   - **Drop the `cp313→cp312` sed and the `python_version` marker rewrite**; the URLs
     are named directly in the manifest and upstream markers evaluate on the real
     py312 interpreter. This deletes the silent-rot class (incl. the `>=` blind
     spot). The stable-token strip of owned lines is retained as belt-and-braces; on
     the current `v4.9` file it is largely inert (no bare torch lines; accel lines
     are `python_version=="3.13"`-gated), so it is defensive, not load-bearing.
   - Keep the existing torch drift-guard as a post-assertion.
   - **HARD build-time ABI gate** (CPU-runner-safe — empirically verified on a
     GPU-less host: the compiled `.so`s resolve against torch 2.9.1 needing only the
     torch-wheel-bundled CUDA libs, not `libcuda.so.1`). Exact incantation, torch
     first, bare compiled modules, plus a `sys.modules` assertion:
     ```
     python -c "import torch; import exllamav3_ext; import flash_attn.flash_attn_interface; import sys; \
       assert 'exllamav3_ext' in sys.modules and 'flash_attn_2_cuda' in sys.modules"
     ```
     On `ImportError`/`OSError` the **build fails RED**. There is no degrade path, no
     `/.abi_degraded`, no uninstall. `torchao` (a third torch-ABI consumer) is
     installed normally under the torch constraints (pip resolves a build compatible
     with 2.9.1) but is **not** in the hard gate — its CPU-runner import behaviour is
     unverified, and a spurious failure there would manufacture the permanently-red
     workflow condition 1 warns against; it is a documented QA-gate residual instead.
     This invariant is itself linted: **L053** (ERROR) fires if an image ships
     `ROOT/opt/accel-wheels.txt` without both the `--no-deps` install and this import
     gate, with mutation tests proving it bites.
2. **Convention (ADR 0002 backfill):** new guarded
   `ROOT/etc/vast_boot.d/05-oobabooga-env.sh` baking `PORTAL_CONFIG` with entries
   Instance Portal `1111:11111`, Jupyter `8080:18080`, Jupyter Terminal `8080:8080`
   (equal-port tab), **Text Generation WebUI `<UI_EXT>:17860`**, **Oobabooga API
   `<API_EXT>:15000`**; Dockerfile `EXPOSE 1111 8080 <UI_EXT> <API_EXT>`.
   `<UI_EXT>`/`<API_EXT>` are set to the **live published Vast template's external
   port values** (pending from the owner) — must be real distinct integers before
   merge (`portal.py:74` `int()` rejects placeholders and breaks `lint --all`).
   Satisfies L050/L051/L052 by the ADR 0002 set-model.
3. **CI:** new `.github/workflows/build-oobabooga.yml`, **amd64-only single arch**
   (`ubuntu-latest`), single base
   `vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312-2026-06-15`, `OOBABOOGA_REF`
   **pinned to the manifest-matched release tag** (`DEFAULT_OOBABOOGA_REF=v4.9` in
   the workflow env; manual override allowed). The pin is deliberate: the
   `accel-wheels.txt` manifest is validated against that exact tag's requirements,
   so REF and manifest move together under review (condition 5) rather than an
   unattended auto-latest that would outrun the manifest. Scheduled runs rebuild the
   pinned tag against the latest base (picks up base bumps). Clones the amd64-only
   single-app workflow + the shared `build-arch-image` / `merge-arch-manifests` /
   `notify-slack` composite actions.
4. **Runtime bind gate:** **deferred to the QA gate** (no GPU-class runners exist);
   recorded explicitly here, not silently skipped. See Binding conditions — this is
   a scoped, time-boxed **partial amendment of ADR 0002 binding condition 1**.

## Binding conditions

Surviving conditions from the final red-team gate. If any is refused, the decision
is void.

1. **The ABI gate uses the exact incantation above** (`import torch` first; bare
   `exllamav3_ext` / `flash_attn.flash_attn_interface`, never top-level
   `import exllamav3`; `sys.modules` assertion). A casually
   written gate ships a permanently-red workflow (false-negative on
   pydantic/rich/formatron skew) or a silent no-op. Empirically verified
   CPU-runner-safe.
2. **`--no-deps` on the accel manifest is mandatory** and is the mechanism that
   prevents the torch downgrade; the uv `-c constraints.txt` is the structural floor
   behind it. Neither may be dropped "to simplify."
3. **Placeholder ports are replaced with the live template's real integers before
   merge**, and `imagegen lint --all` must report the baseline CLEAN.
4. **Partial amendment of ADR 0002 binding condition 1 (bind smoke gate).** Because
   no GPU-class CI runner exists, the runtime `ss -ltnp` 0.0.0.0-bind gate is
   **owned by the QA gate** until GPU runners land, at which point it is wired into
   `build-oobabooga.yml`. To keep this honest rather than a "green check shipping the
   regression," two compensating controls are required now: (a) **make
   `oobabooga.sh`'s default additive-safe** so a launch template cannot *drop* the
   loopback `--listen-port/--api-port` default (closing the
   `${OOBABOOGA_ARGS:-…}` full-replacement → `--listen` → `0.0.0.0` escape hatch on
   the EXPOSEd ports); (b) the QA-gate checklist explicitly includes the in-container
   `ss -ltnp` bind check for ports 17860/15000 before publish. Static `L051` +
   the verified loopback default remain the fast layer.
5. **Pre-decided fail-RED runbook.** When upstream and the base torch fall out of ABI
   lockstep (a *when*, given the exact-patch wheel pin), the gate fails the whole
   build. The reviewed escape is the **manifest**: drop the offending wheel line to
   ship WebUI-only (the UI + llama.cpp backend do not need exllamav3), or pin
   `OOBABOOGA_REF` back, rather than relax the gate or block a base/security bump.

## Consequences

- **Positive:** the build becomes green *and proven* (real compiled-ABI check, not
  an assertion); the silent-`sed`-rot class is deleted; the accel matrix is an
  explicit, reviewed, greppable manifest pinned to a release tag; the image
  self-describes its ports (L050/L051/L052 clean); oobabooga gains CI (L030);
  amd64-only matches wheel reality.
- **Accepted-negative:** the owned manifest must be reconciled on each deliberate
  `OOBABOOGA_REF` bump (visible, reviewed work). The ABI gate covers exllamav3 /
  flash_attn / torchao but cannot prove kernel *execution* on a CPU runner — that
  residual, and the network-bind residual, are owned by the QA gate until GPU
  runners exist. fail-RED-always couples "broken accel ABI" to "broken image" by
  design (mitigated by the runbook, condition 5).

## What would reverse this

- If `import exllamav3_ext` / `flash_attn_2_cuda` ever require a GPU/driver at import
  on `ubuntu-latest` (a future wheel linking `libcuda.so.1` directly), the hard CI
  gate must move to a GPU/QA step — the empirical CPU-safety premise collapses.
- If a future base torch bump breaks 2.9.x patch-ABI compat for these wheels, the
  "install anyway" premise is void → invoke the condition-5 runbook.
- If GPU-class runners arrive, condition 4's deferral ends: wire ADR 0002's bind
  gate into `build-oobabooga.yml` and drop the QA-gate ownership.
- If Vast's EXPOSE auto-map behaviour changes, revisit the convention half per
  ADR 0002.
