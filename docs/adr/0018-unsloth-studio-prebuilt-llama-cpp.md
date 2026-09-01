# ADR 0018 — unsloth-studio installs llama.cpp from the Unsloth fork's prebuilt bundle

- **Status:** Accepted
- **Date:** 2026-08-28
- **Decision owner:** Rob Ballantyne

## Context

`unsloth-studio` source-built llama.cpp inside `docker build`, and every part of that
was expensive or fragile:

- **A 20–40 minute CUDA compile** on every image build, triggered by an *unsloth*
  release that usually had nothing to do with llama.cpp.
- **A fabricated `nvidia-smi`.** The studio's `setup.sh` gates `-DGGML_CUDA=ON` on a
  runtime GPU probe that `docker build` cannot satisfy, so without a stub binary
  answering `-L` it silently produced a CPU-only llama.cpp and every inference ran on
  CPU. That is ADR 0016's defect, and the stub is the workaround, not a fix.
- **A hand-pinned arch list** (`80;86;89;90;100;120`) with no `sm_75`, which blocks
  lowering the template's `compute_cap` floor to Turing.
- **A binary that could not report its own version.** A `--depth 1` clone breaks
  `build-info.cmake`, so `llama-server` answered `version: 1`.

Meanwhile ADR 0033 moved the `llama-cpp` derivative to the Unsloth fork's **prebuilt
portable bundles**, with the asset named directly and verified by sha256 against the
release's own `llama-prebuilt-sha256.json`. That work has shipped, promoted, and passed
live-GPU QA. The same artifact family serves this image.

Separately, this image had **no QA gate of any kind** — no template, no instance tests,
no `qa` job — so a green `docker build` was the entire bar. ADR 0001 is explicit that a
build is not the correctness check, and this is the image whose CUDA backend has already
regressed to CPU once.

## Options considered

**A. Keep the source build, fix it in place.** Rejected. It leaves the compile time,
the fabricated probe and the version-reporting defect, and the arch list stays a hand-
maintained literal that must be re-reasoned every time the GPU floor moves.

**B. Prebuilt bundle, asset resolved through the release manifest by a composite
action.** This was the original design (PR #234, July 2026): a `resolve-llama-bundle`
action picked the asset by `coverage_class` from `llama-prebuilt-manifest.json`,
cross-checked the checksum file's self-reported release tag, and emitted tag + asset +
sha256 as CI outputs. Rejected **not because it is wrong** — it is more defensive than
what was chosen — but because ADR 0033 subsequently shipped a simpler path to the same
artifact, and carrying two mechanisms for "fetch and verify an unsloth bundle" is a
maintenance cost with no user-visible benefit. Recorded as the stronger option on
supply-chain grounds should we ever want manifest-driven selection across both images.

**C. Prebuilt bundle, named directly and hash-verified (chosen).** Mirrors
`derivatives/llama-cpp` exactly: construct `app-<ref>-linux-x64-cuda12-portable.tar.gz`,
fetch it with `llama-prebuilt-sha256.json`, verify before extracting.

## Decision

Take option **C**. `unsloth-studio` fetches the CUDA-12 portable bundle from the Unsloth
fork, verifies its sha256, extracts it with Python's `data` filter, and points
`unsloth studio setup` at it via `UNSLOTH_LOCAL_LLAMA_CPP_DIR` so the studio reuses the
tree instead of downloading or compiling one.

The bundle version is **resolved from the publisher** at CI time via the existing
`check-github-release` action, with `tag-pattern: '-mix-'` — the same call
`build-llama-cpp` already makes against the same repository. A dispatch may name an exact
release through `LLAMA_CPP_VERSION`, which is the override path, not the default one.

A literal in the workflow was considered and rejected: it goes stale silently, and every
rebuild then keeps shipping whatever bundle happened to be current the day someone last
edited the file — which is the same class of decay as a stale base pin, arrived at by
hand instead of by tag.

The resolver deliberately does **not** feed `should-run`. The build TRIGGER stays a new
*unsloth* release; a new llama.cpp is not on its own a reason to rebuild this image, and
wiring it in would let an upstream cadence we do not control drive our build schedule.
The resolver only answers *which bundle should the build that is already happening bake*.

The accepted cost: two builds of the same unsloth version can bake different llama.cpp
bundles, so a rebuild of an existing version is not byte-reproducible. That is bounded —
scheduled builds only run on a NEW unsloth release, so same-version rebuilds are manual —
and the mitigation is unchanged: a manual rebuild **must** set `CUSTOM_IMAGE_TAG` so a
published tag never changes content under the same name.

`aio-studio` keeps its source build. Only the assertion half of this change applies
there, because the failure it catches does not depend on where the binary came from.

## Binding conditions

1. **Verify before extract, and fail on a missing manifest entry** rather than comparing
   empty-to-empty — the sha256 of no input is a constant, so two failed lookups compare
   equal and the check passes having verified nothing.
2. **Extraction is hardened** with Python's `tarfile` `data` filter (no absolute paths,
   no `..`, no links escaping the destination, no setuid). The studio's own installer
   applies that filter when it unpacks this bundle; bypassing the installer means owing
   the same hardening.
3. **The reuse branch must be PROVEN, not inferred** — the canonical path resolves to our
   tree *and* the binary is byte-identical to the one whose hash we verified. Neither
   alone suffices: the canonical path is a symlink into our tree, so a re-download would
   land on top of it and still satisfy a `readlink` and a marker-file check.
4. **The guard is replaced, not inherited.** `test -f …libggml-cuda.so` is satisfied by
   `tar x` once the binary is not compiled here, so L056 is amended to require `ldd -r`
   *with its output inspected*, and L081 requires `cuobjdump --list-elf` against literal
   `sm_NN` targets read from the artifact.
5. **llama.cpp is image content, not user data.** The boot hook repoints
   `$WORKSPACE/unsloth/llama.cpp` at the image copy every boot, because the workspace
   sync only copies when the target is absent and a reused volume would otherwise run an
   older image's llama.cpp regardless of what this image contains.

## Consequences

**Positive.** The compile disappears. The fabricated `nvidia-smi` disappears. The binary
reports a real build number. The bundle carries SASS down to `sm_75`, which makes a
Turing floor reachable. The image gains its **first QA gate**: a template, an
`unsloth.d/` suite asserting the studio serves on loopback behind a portal entry, and a
runtime check that the CUDA backend is usable on the rented host.

**Accepted negatives.** We now trust a third-party binary we did not build. The sha256
check detects transport damage, **not a compromised publishing path** — the hashes are
published by the same party as the asset. The `merged_prs` provenance in
`UNSLOTH_PREBUILT_INFO.json` is publisher-declared: evidence of intent, not of content.

**Deliberately not done here.** The `compute_cap` floor stays at 800. The bundle makes
Turing reachable and the build asserts `sm_75`, but admitting a GPU class this gate has
never drawn is a separate step, gated on a live run on Turing hardware. Image first,
floor second.

**Scope of the runtime check.** `unsloth.d/11-llama-offload` proves the backend *loads*
on the host — it cannot prove a given training run offloaded, because the studio invokes
`llama-server` for a job rather than running it as a service, so there is no long-lived
pid to attribute VRAM to. The `-ngl 0` / partial-offload family stays out of reach
without starting a server and a model.

## What would reverse this

The fork ceasing to publish portable bundles or checksum manifests; a bundle whose CUDA
major stops matching this image's torch line; or evidence that the published hashes are
not trustworthy, which would move the decision back toward option B or to building from
a pinned source tree.
