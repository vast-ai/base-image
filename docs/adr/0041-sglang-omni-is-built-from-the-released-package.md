# ADR 0041 — SGLang-Omni is built from the released package, not from an upstream image

- **Status:** Accepted
- **Date:** 2026-09-14
- **Decision owner:** Rob Ballantyne

## Context

The catalog has no audio-first inference engine. Text-to-speech, transcription and
omni chat are reachable today only as side capabilities of `vllm-omni`, and speaker
diarization is not reachable at all. SGLang-Omni is a serving framework built for
exactly that band: 22 model implementations, almost all audio, with dedicated
transcription, translation, speech and voice-registry endpoints.

Every inference engine this repo ships is an **external** image: a multi-stage wrap
of a versioned upstream image, converted by `tools/convert-non-vast-image.sh`, with
CI resolving the newest upstream release through `check-dockerhub-release`. `vllm`,
`vllm-omni` and `sglang` are all built this way, and the pattern is good — upstream
owns the native toolchain and we track their releases automatically.

The obvious move was to mirror that. It does not work here, and the reason is
specific rather than general.

### The upstream CUDA image is not a release artifact

SGLang-Omni is an actively developed project. It cut five releases between
2026-08-08 and 2026-09-10, carries dedicated ASR, TTS and omni CI workflows, and
publishes versioned wheels on PyPI. None of that is in question.

What it does not have is a CUDA container anybody builds. The evidence:

- `lmsysorg/sglang-omni` has exactly **one** tag, `dev`, amd64-only, ~18 GB.
- That tag has not moved since **2026-06-16**, confirmed three ways: the registry's
  `last_updated`, the image config's own `created: 2026-06-16T19:47:40Z`, and the
  commit baked into it (`SGLANG_OMNI_COMMIT=bdb6748…`, authored `2026-06-16T15:56:47Z`).
- The project's only docker-publishing workflow, `omni-xpu-docker-release.yaml`,
  pushes `intel/sglang-omni-xpu-dev` — an Intel XPU image, contributed by Intel.
  It was pushed **2026-09-10**, the same day as the current release. Where they own
  a docker release, they keep it current.
- The `sgl-project/sglang` repository contains **zero** references to `sglang-omni`
  anywhere under `.github/`. The Actions run stamped into the `dev` image's labels
  is sglang's own *Release Docker Images* run from 2026-05-23 — the run that built
  the **base** `lmsysorg/sglang:v0.5.12.post1`. The label was inherited, not earned.

So the CUDA image was a one-off manual push. This matters more than staleness would:
a stale artifact is fixed by a bump, whereas an artifact no pipeline builds will not
become current. It also means `check-dockerhub-release` has no tag vocabulary to
watch — though, as option 6 records, that is not the same as having no watcher
available.

Inside that image the engine is an **editable install from a git clone**
(`uv pip install -e .`) into a separate Python 3.11 virtualenv at `/opt/omni`. That
sits outside `/venv/main`, breaking the convention `vllm-omni` and `sglang` rely on,
where a user can `uv pip install` an upgrade and have the engine pick it up.

### The package, by contrast, is exactly what we want to pin

`sglang-omni` ships versioned wheels with a roughly two-week cadence, and the
current release resolves cleanly for linux x86_64 / Python 3.12: 275 packages,
`torch==2.13.0`, `sglang==0.5.19`, `transformers==5.12.1`, no conflicts. We already
publish a `vastai/pytorch` base at that exact torch version.

Wheel coverage is near-total. Seven packages resolve to source distributions, and
all seven are cheap: `logger`, `openai-whisper`, `s3prl`, `sox` and `wget` are pure
Python, `cdifflib` is a small C extension, and `cuda-tile` is a 10 KB NVIDIA stub.

## Options considered

**1. External wrap of `lmsysorg/sglang-omni:dev`, pinned by digest.** Mirrors the
house pattern and lets upstream own the native toolchain. Rejected: it pins an
artifact no CI builds, so the release-tracking and bump mechanisms that make the
external pattern safe do not apply; it ships a pre-release commit of the engine; it
carries ~18 GB before our overlay; and it puts the engine in a Python 3.11 venv
outside `/venv/main`.

**2. Pytorch-nested from `vastai/pytorch`, installing a pinned release.** Chosen.

**3. External wrap, then upgrade the package in place.** Superficially the best of
both — upstream's prebuilt kernels plus a version we control. Rejected: the current
release requires `sglang==0.5.19` while the image ships `0.5.12.post1` with
`sgl-kernel` and flashinfer built against it, so the upgrade would pull a different
kernel package and invalidate precisely the prebuilt work the image was chosen for.
We would carry 18 GB of toolchain and then discard its value.

**4. The full `vastai/pytorch` base instead of `-mini`.** Rejected on house
convention, not on a per-image calculation. A purpose-built image does not ship a
full development environment; full builds exist for `base-image` itself and for the
pytorch image's full branch. All 15 existing images under
`derivatives/pytorch/derivatives/` build on `-mini`, so this is a rule the repo
already follows everywhere and simply has not written down. The size difference here
is 5.06 GB against 10.16 GB compressed, which is the reason the rule exists rather
than an argument specific to this image.

**5. Defer until upstream publishes versioned CUDA images.** Rejected as a blocker,
retained as a trigger. Option 2 does not depend on upstream changing anything, so
waiting buys nothing; but if upstream later ships a CI-built versioned image, that
is the event that reopens option 1 (see *What would reverse this*).

**6. Track the engine version by hand, with no watcher.** Rejected, and it was the
first draft of this decision. `.github/actions/` already contains
`check-pypi-release` (used by `build-unsloth-studio.yml`) and `check-github-release`
(used by `build-comfyui.yml`). SGLang-Omni publishes versioned PyPI wheels on a
two-week cadence, which is exactly the shape `check-pypi-release` consumes. A pin
with no watcher on a project releasing fortnightly becomes stale in a way nobody
sees; this repo has already paid for that once, when a set of derivative base pins
went stale together and needed a bulk sweep. "Deliberate" and "forgotten" are
indistinguishable after four months.

**Recorded dissent.** The design review did not agree that this image is the next
thing to build. Measured against the engines already in the catalog, SGLang-Omni's
unique surface is narrow: one endpoint variant (`/v1/audio/translations`) and eight
model implementations, of which the genuinely distinct ones are ASR-side. The
counter-position is that the larger catalog gain is exposing what the current engines
already serve — the capability fragments declare `[chat, completions, models]` for an
engine that also serves speech, images, video, transcriptions and embeddings — and
that this image should follow that work rather than precede it. Speaker diarization,
which nothing in the catalog can do, is the strongest single argument on the other
side. This ADR records the construction decision; the sequencing question is
deliberately left open.

## Decision

**SGLang-Omni ships as a `pytorch-nested` image built from a pinned upstream
release, not as an external wrap of an upstream container. It ships on-demand only;
serverless is explicitly out of scope until a worker exists (see below).**

It is the first inference engine in this repo that is not an `external/` image, so
it must adopt the engine conventions explicitly rather than inherit them from that
directory's shape.

### Construction

- Lives at `derivatives/pytorch/derivatives/sglang-omni/`, scaffolded with
  `imagegen new --class pytorch-nested`.
- `FROM vastai/pytorch:2.13.0-cu130-cuda-13.2-mini-py312-<date>`.
- The **`-mini`** variant, per option 4. The CUDA toolchain is present but off
  `PATH` and the math development headers are stripped, so the image exports
  `CUDA_HOME`, puts the toolchain on `PATH`, and installs the `cuda-*-dev` packages
  the JIT path needs. The prebuilt kernel artifacts below are what keep that list
  small.
- The **`cu130`** torch wheel on the CUDA 13.2 toolkit, not `cu132`. This is chosen
  to match the flashinfer kernel cache line exactly — see below — and the choice is
  currently **not expressible** to the resolver, which is a defect this image is the
  first to encounter (see binding condition 4).
- The engine installs into `/venv/main`, so the upgrade convention holds and the
  model-ui wiring carries over unchanged.
- **amd64 only** initially. Every heavy dependency does publish aarch64 wheels, so
  this is a scope decision rather than a hard block, revisited once amd64 passes its
  gate.

### The flashinfer kernel artifacts come from flashinfer, by their own CLI

This is easy to get wrong, and the first draft of this ADR got it wrong twice.
flashinfer publishes two artifacts from `https://flashinfer.ai/whl/`, backed by
their GitHub releases, and **PyPI carries a stale partial copy of one of them** —
PyPI's `flashinfer-cubin` stops several releases short of what this engine pins.
PyPI must not be treated as the source of truth for either.

- **`flashinfer-cubin`** — precompiled kernel binaries for all supported GPU
  architectures. CUDA-version-independent, pure-Python wheel.
- **`flashinfer-jit-cache`** — the prebuilt kernel cache, published per CUDA line.
  At the pinned engine's flashinfer version the `cu130` line carries 118 wheels
  through the current release; `cu134` carries 6. **`cu130` is the line with
  history**, which is why the base pin above selects the `cu130` torch wheel and why
  the cache is an exact match rather than a nearest-neighbour guess.

Both are installed through flashinfer's own CLI — `flashinfer install-cubin-wheel`
and `flashinfer install-jit-cache-wheel --cuda-version <line>` — rather than through
hand-written index URLs. Upstream owns the mapping from CUDA line to artifact; a URL
pinned here would be a second copy of that mapping, and it would rot on a bump.

### The engine version is pinned by us, and watched

The package version is a first-class pin in the Dockerfile. The build workflow uses
`check-pypi-release` to detect a new upstream release, on the same terms as the
images that already do this. Detection is automatic; taking the bump stays a
reviewed change.

### The bind is set by the image, not delegated to the template

`sgl-omni serve` defaults to `--host 0.0.0.0`. The `sglang` image can leave the bind
to `SGLANG_ARGS` because SGLang's own default is already loopback; that reasoning
does not transfer. The launch script supplies `--host 127.0.0.1 --port 18000`
itself, and the contract test asserts the bind rather than trusting the launch line.

**This interacts with L078**, which maps a backend to its engine-args variable and
requires the gating QA template to carry the bind flags in that variable. There is no
mapping for this engine, and the decision above puts the flags in the launch script
rather than in a template variable. L078 must gain an entry for this engine that
reflects where the bind actually comes from — satisfying it by pasting flags into a
variable the launcher never reads would be compliance by cosmetics, which is the trap
L078's own docstring documents.

### The capability fragment must not claim a fixed endpoint set

SGLang-Omni registers routes **conditionally on the loaded model**. Upstream states
plainly that one ASR model returns HTTP 400 on `/v1/audio/translations`, and the
source carries a per-model capability registry. A static fragment listing every route
this engine can serve would advertise endpoints that 400 on most models — the same
defect the current fragments have in the opposite direction. The fragment declares
only what holds for every model, and the per-model surface is reported from the
running server.

### Serverless is out of scope, and this is a dependency, not an omission

The serverless path cannot work for this engine today, and the reason is structural
rather than a matter of wiring:

- The shared OpenAI worker registers two handlers, and its benchmark is bound to
  `/v1/completions`. **SGLang-Omni does not register that route.** Its route table is
  `/v1/chat/completions`, `/v1/audio/*`, `/v1/models`, `/health`, `/generate` and the
  realtime sockets. Every benchmark request would 404, the worker would report no
  successful responses, and the score file would never be written.
- ADR 0031's serverless assertion is that the score file was written *by this run*,
  so that cell would fail deterministically — on every CUDA variant, with ADR 0029
  redrawing each failure. That is a redraw loop burning rentals on every build.
- `start_server.sh` exits when `BACKEND` names a worker directory that does not
  exist, and there is no worker for this engine.

So the image ships **on-demand only**. It bakes no `BACKEND`, no `EXPOSE 3000` and no
serverless-specific env, because declaring a mode that cannot work is worse than not
declaring it. Serverless is enabled in a later, separate change once the worker
exists, and that change owns three things this ADR does not: a worker for this
engine, a benchmark bound to a route the engine actually serves, and the model-name
resolution (the worker's variable list is a hardcoded tuple that does not include
this engine's model variable).

Recording this as scope rather than as a gap is deliberate. The first draft asserted
that the serverless wiring was "the established shape" and that ADR 0031's gate
applied unchanged. Both were false, and the second was load-bearing for the base
variant decision — which is why option 4 above is now argued from house convention
and not from cold-start latency.

### The gate is ADR 0031's, minus the cell that cannot pass

Contract, worker and capability, on-demand cells across the CUDA matrix. The
serverless cell arrives with the serverless change.

## Binding conditions

1. **The base image's torch is not replaced.** The base ships torch 2.13.0 built for
   a specific CUDA line; the engine's metadata asks for a bare `torch==2.13.0`, which
   a naive install satisfies from the default index with a differently-built wheel.
   The build asserts that the torch present after the install is the one the base
   shipped. A silent swap builds green, passes a shallow smoke test, and fails on a
   rented GPU.
2. **The prebuilt kernel artifacts are asserted present at build and asserted
   EFFECTIVE at gate.** No kernel warm is required at build time, and none is
   possible: every CI runner is GPU-less, and flashinfer selects architectures from
   the live device. A warm would silently degrade to a no-op and build green, which
   is a defect this repo has already shipped once and written into its invariants.
   Instead: the build asserts both artifact packages are installed at the pinned
   version and CUDA line and that they import; the live-GPU gate asserts
   effectiveness with `flashinfer module-status`, so "cache installed but ignored and
   recompiled from scratch" — the silent outcome a build-time check cannot see — is
   caught where a GPU exists.
3. **The `cuda-*-dev` package list is justified by what is NOT covered.** It exists
   only for kernels the cubin and jit-cache packages do not supply. If that set turns
   out to be empty the list should be too; if it is large, the artifact plan is not
   working and should be re-examined before the list grows.
4. **The base pin is unambiguous.** DISCHARGED before this image was scaffolded.
   `basetag.select_latest`/`resolve` now take the torch cuda-wheel and `bump` passes the
   pin's own wheel back in, so a bump floats the date and nothing else; omitting the wheel
   where the remaining coordinates still admit more than one now raises instead of letting
   the pick fall to registry listing order. Gated by **L096** and recorded in
   `docs/invariants.md`. The pin in this ADR names its wheel (`cu130`) for that reason.
5. **The capability fragment is verified against a running server**, not written from
   the upstream route table.

If any condition is refused, the decision is void rather than amended in place.

## Consequences

- We own the dependency resolution that upstream would otherwise own. That is the
  accepted cost, and it buys a version pin on the artifact upstream actually
  maintains, plus a watcher for it.
- The engine image family is no longer coextensive with `external/`. Note that image
  class in this repo is **inferred from the directory path** by `discover()`, so
  "class" and "directory" are the same fact — anything that reasons about engines by
  looking for `external/` needs to learn this image's path, and there is no declared
  class to consult instead.
- The image ships without serverless support, which is the mode the catalog is moving
  toward. That is a real gap for as long as it lasts, and it is visible rather than
  latent because nothing in the image claims otherwise.
- The `cuda-*-dev` package list is a new maintenance surface tied to what the JIT
  path reaches for, so an engine bump can change it. Binding condition 2's gate-side
  assertion is what makes that visible.
- Two SGLang versions exist in the catalog — the `sglang` image tracks its own
  releases, this one tracks whatever `sglang-omni` pins. They are separate images and
  do not interact.

## What would reverse this

Upstream building and publishing versioned CUDA images from CI, on a cadence matching
their releases — the thing they already do for the Intel XPU image. That restores
both mechanisms the external pattern depends on, and at that point option 1 is the
better construction and this ADR should be superseded rather than quietly worked
around.
