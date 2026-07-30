# ADR 0018 — Unsloth Studio: ship llama.cpp as an upstream prebuilt bundle

- **Status:** Accepted (conditional)
- **Date:** 2026-07-30
- **Decision owner:** Rob Ballantyne
- **Amends:** [ADR 0016](0016-unsloth-studio-gpu-llama-and-login.md) (llama.cpp build mechanism and
  GPU floor; the login decision is untouched)

## Context

`unsloth-studio` bundles llama.cpp for GGUF inference and export. ADR 0016 made that a
**source build**: a build-only stub `nvidia-smi` satisfies the studio's runtime GPU probe,
`UNSLOTH_LLAMA_CUDA_ARCHS="80;86;89;90;100;120"` pins the arch list, and a post-build
assertion fails the build if `libggml-cuda.so` is missing or lacks SASS for the bracket
arches. That fixed a real defect (a silent CPU-only binary) and still works.

Two things now push against it.

**1. The arch list cannot serve a lower GPU floor.** ADR 0016 set `compute_cap gte 800` and
tied floor and arch coverage together. Lowering the floor to Turing (`gte 750`) requires
`sm_75` coverage, which the current arch list does not have. The base image's torch does:
`torch 2.10.0+cu128` reports `sm_70 sm_75 sm_80 sm_86 sm_90 sm_100 sm_120`, so Turing is
supported by the rest of the image already.

**2. The image cannot state what llama.cpp it contains.** The studio's `setup.sh` resolves
a forced source build to branch `master` of `ggml-org/llama.cpp` and clones it with
`--depth 1`. That shallow clone breaks llama.cpp's `build-info.cmake`
(`git rev-list --count HEAD` = 1), so the shipped binary reports `version: 1 (<sha>)`
instead of a build number, and a source build writes no install marker. The commit sha is
still present in the version string, so identity is recoverable, but nothing in the image
records it and nothing in CI reports it.

The prompting observation was that the image often carries a llama.cpp weeks behind
upstream. That was traced to the build **cadence**, not the recipe: the workflow only builds
when a new `unsloth` PyPI release lands, while llama.cpp cuts releases many times a day.
Staleness is addressed by policy in this ADR (see Decision 2), not by the build mechanism.

Upstream publishes an alternative. `unslothai/llama.cpp` — the fork the studio's own
installer pulls from — ships per release a manifest plus prebuilt bundles. The
`linux-x64-cuda12-portable` bundle was downloaded and inspected directly:

- `BUILD_INFO.txt` declares `supported sms: 70,75,80,86,89,90,100,103,120`,
  `ggml_cpu_all_variants: ON`, `ggml_backend_dl: ON`, `rpath: $ORIGIN`, toolkit 12.8.
- `cuobjdump --list-elf` on the actual `libggml-cuda.so` finds SASS for
  **sm_70 75 80 86 89 90 100 120** — **`sm_103` is absent despite the manifest claiming it.**
  `cuobjdump --list-ptx` finds PTX for all of the same arches, so `sm_103` and `sm_121`
  reach kernels by JIT rather than by SASS. Upstream metadata overstates coverage; only the
  binary is authoritative.
- `NEEDED libcudart.so.12, libcublas.so.12, libcuda.so.1`, with no bundled CUDA.
- 14 `libggml-cpu-*.so` microarch variants, making the `-DGGML_NATIVE=OFF
  -DGGML_CPU_ALL_VARIANTS=ON` patch this repo applies to `setup.sh` unnecessary.
- `llama-server --version` reports `version: 10181 (71c50d040)`, and the bundle ships its
  own install marker recording upstream tag, source commit and claimed arch set.

Two further findings shaped the decision. First, the studio's prebuilt installer **cannot**
be used unmodified inside `docker build`: CUDA bundle selection requires a
`driver_cuda_version`, parsed only from a real `nvidia-smi` banner, and upstream documents
the GPU-less case as "fall back to a source build". Second, `setup.sh` has a first-class
branch for a caller-supplied tree — when `UNSLOTH_LOCAL_LLAMA_CPP_DIR` names the canonical
install location and a `llama-server` is already there, it skips both the prebuilt download
and the source build.

A structured design review was run before building, with independent architecture,
operations, security and adversarial critiques. Their surviving findings are recorded as
binding conditions below.

Relevant prior art: [ADR 0016](0016-unsloth-studio-gpu-llama-and-login.md) (this amends it),
[ADR 0001](0001-image-scaffolding-tooling.md) (static checks are a shape gate; the real
build plus live-GPU run is the correctness gate), [ADR 0013](0013-base-tag-resolution.md)
and the repo convention that upstream versions are resolved in CI and passed as build args,
[docs/invariants.md](../invariants.md).

## Options considered

**1. Keep the source build (status quo).** Rejected: no `sm_75`, so it blocks the floor
change; it keeps the compile toolchain, the fabricated GPU probe, our own CPU-dispatch
patch and a 20-40 minute compile; and it produces a binary that cannot report its own
version.

**2. Satisfy the studio's resolver at build time.** Extend the stub `nvidia-smi` to also
print a `CUDA Version:` banner so upstream's installer selects and installs a bundle for us.
Rejected on three counts. It adds a second fabricated signal, and inverts a deliberate
fail-closed property: today the stub answers **no** compute capability precisely so that a
future upstream change fails the build rather than silently narrowing the arch set; under
this option the same missing signal is relied on to mean "prefer the portable bundle", so
the same input would carry two contradictory safety arguments. Image content becomes a
function of a remote resolver at build time, so the Dockerfile no longer states what it
ships. And arch coverage becomes emergent, which cannot support a floor move.

Recorded dissent: on **integrity** alone this option is the stronger one. Upstream's
installer does traversal-safe extraction, selects assets by lookup in an approved checksum
manifest, and cross-checks the checksum asset's self-reported release tag — none of which a
hand-rolled fetch gets for free. The security critique's position was that if our own fetch
is not hardened to that standard, this option is strictly better than the chosen one. That
is why hardened extraction is a binding condition rather than an implementation detail.

**3. Fetch upstream's published bundle and hand it to the studio (chosen).** See Decision.

**4. Pin the llama.cpp release in the Dockerfile.** Rejected: it makes this image an
outlier against the repo convention that upstream versions are resolved in CI and passed as
build args (the `unsloth` version itself, and `LLAMA_CPP_VERSION` in the `llama-cpp`
derivative, both work this way), and it creates a manual bump treadmill. The cost of
rejecting it — no human review window on a third-party release — is accepted explicitly
under Consequences.

**5. Keep the source build and add a llama.cpp release trigger.** Rejected: the fork
publishes at llama.cpp cadence, so a release-driven trigger would rebuild and republish
almost every poll. The demand signal that actually matters — support for a new model or
architecture — reaches this image as an `unsloth` PyPI release, which already triggers a
build on the 12-hour cadence. Bumps for any other reason (a llama.cpp CVE, a performance
regression) are handled by `workflow_dispatch`, which is also the path that carries the
production environment approval gate.

**6. Publish a CUDA 13 line alongside 12.9.** Deferred. `vastai/pytorch` already publishes
cu130 bases and `torch 2.11.0+cu130` still covers `sm_75`, so the floor decision is
unaffected either way. It is not needed for a CUDA 12.9 image, it narrows the rentable
fleet to r580+ drivers, and it doubles the build and QA matrix.

**7. Only accept a fork release older than N hours (quarantine).** Considered as partial
compensation for option 4's rejection; not adopted, to avoid adding a bespoke trigger rule.
Recorded here so a future reader knows it was weighed.

## Decision

1. **Ship the upstream prebuilt bundle.** CI resolves the newest usable
   `unslothai/llama.cpp` release and passes three values as build args: release tag, exact
   asset name, and expected sha256, selected in the preflight job by lookup in the release's
   manifest and checksum assets on `install_kind=linux-cuda`, `coverage_class=portable`, and
   the runtime line matching the base image's CUDA major. The Dockerfile fetches exactly that
   asset, verifies exactly that hash, and constructs no filenames of its own. It then places
   the tree where the studio expects it and runs `unsloth studio setup` with
   `UNSLOTH_LOCAL_LLAMA_CPP_DIR` so setup reuses it instead of downloading or compiling.
   The stub `nvidia-smi`, `UNSLOTH_LLAMA_CUDA_ARCHS`, the CPU-dispatch patch and the CUDA
   **dev** packages are removed; the CUDA **runtime** packages are not.
2. **No llama.cpp rebuild trigger.** The image continues to build only when a new `unsloth`
   release lands, plus `workflow_dispatch` for exceptional bumps. A dispatched bump that
   changes only llama.cpp **must** set `CUSTOM_IMAGE_TAG`, so a published tag never changes
   content under the same name.
3. **CUDA 12.9 only for now**, but the bundle's runtime line is derived from the base image
   tag rather than hardcoded, so a future cu130 base is a build-arg change.
4. **llama.cpp leaves the workspace-synced tree.** The binaries are image content, so they
   move outside `/opt/workspace-internal`, following the pattern `aio-studio` already uses.
   This is in scope here because the boot sequence otherwise defeats the decision: the
   workspace sync copies only when the target does not exist, and the studio's symlink hook
   then repoints the install path at `$WORKSPACE`, so an instance reusing a volume runs the
   old tree and reports the old marker no matter what the image contains.
5. **Assertions replace, not inherit, the ADR 0016 guard.** `test -f …libggml-cuda.so` is
   satisfied by extraction and proves nothing once we stop compiling, so the build must
   additionally prove that every `NEEDED` of `libggml-cuda.so` resolves inside the image
   (`ldd -r`), that `llama-server` actually executes (the fork links it against OpenSSL,
   which the source build never needed, so a binary can be present and unloadable), and
   that the shipped binary carries SASS for **every** admitted arch — enumerated, not
   bracketed: nothing guarantees that covering the ends covers the middle, and this bundle
   itself proves the vendor's declared set can be wrong. Read from the artifact
   (`cuobjdump --list-elf`), never from the manifest. `cuda-cuobjdump` is retained for
   this; it needs no nvcc or dev headers.
6. **GPU floor moves to `compute_cap gte 750` for `unsloth-studio` as a sequenced
   follow-up**, after an image with verified `sm_75` coverage is published and passes a
   live-GPU run on Turing. Image first, floor second: the reverse order puts renters on a
   binary without kernels for their card. `aio-studio` keeps `gte 800` and its source build
   until decided separately — it carries the same unsloth block and the same arch list.
7. **arm64 remains out.** `torchao` publishes no aarch64 wheel with compiled extensions
   (only a pure-Python fallback containing no shared objects), and unsloth gates on the
   module being importable, so an arm64 build would install cleanly and lack kernels
   silently. Independently, the fork's only arm64 bundle is a CUDA 13 build covering
   `sm 90+`, which does not fit this image line.
8. **Record what shipped.** The release is surfaced as the `LLAMA_CPP_RELEASE` **ENV**
   (not a label: linter rule L001 pins the Dockerfile to exactly three LABEL keys), and
   the bundle's full install marker — upstream tag, source commit, declared arch set —
   ships at `/opt/llama-cpp/UNSLOTH_PREBUILT_INFO.json`. Both are readable from
   `docker inspect` or from inside the instance, without running the binary.

## Binding conditions

If any of these is refused, the decision is void and the source build stands.

- **Hardened extraction.** Reject absolute paths, `..` traversal, links pointing OUTSIDE
  the destination, device nodes and setuid bits, and do not preserve owner. Note that
  links *within* the destination must be allowed — the bundle ships internal soname
  symlinks, so a blanket symlink refusal would reject every bundle. Python's `tarfile`
  `data` filter is exactly this policy. Cross-check the checksum asset's self-reported
  release tag against the resolved tag, and refuse an asset name, tag or digest whose
  shape could inject a line into the CI output it flows through. This is the price of not
  using upstream's installer, and the recorded dissent above depends on it.
- **Both new assertions present and proven to bite**: `ldd -r` clean, and the `cuobjdump`
  SASS bracket taken from the artifact. With `ggml_backend_dl: ON` an unresolvable
  `libggml-cuda.so` is skipped at load time and inference silently falls back to CPU — the
  exact defect ADR 0016 exists to prevent, moved from build time to runtime where no
  existence check can see it.
- **Codified, not just described.** Amend linter rule L056 so the required assertion is
  resolution-plus-arch rather than file existence, with a mutation test proving the new
  check fires on an empty `libggml-cuda.so`; regenerate `docs/lint-rules.md`; update
  [docs/invariants.md](../invariants.md); the `imagegen lint --all` baseline stays clean.
- **The install path must not contain the string `cuda`.** The base boot hook that
  configures CUDA deletes `/etc/ld.so.conf.d/*cuda*.conf` and strips matching lines from the
  remaining files, so a natural bundle directory name would silently break library
  resolution at boot. Library resolution is scoped to the app's own launch rather than added
  to the container-wide search path.
- **A real `docker build` plus a live-GPU run confirming GPU offload** before the production
  tag is promoted, and a Turing run before the floor moves (ADR 0001: the build assertions
  prove properties of the artifact, not that inference offloads).
- **Licensing.** Redistributing llama.cpp as a binary still carries its MIT notice;
  `ROOT/LICENSES.md` gains the entry, and the existing modifications paragraph is corrected
  where the removed `setup.sh` patches made it stale.
- **The imagegen spec** for this image describes none of the ADR 0016 or ADR 0018 build
  steering; it is corrected or explicitly marked non-authoritative in the same change, so
  regenerating the image cannot silently discard either fix.

## Consequences

- GGUF inference and export run on a tested upstream binary covering `sm_70` through
  `sm_120` in SASS, with PTX for JIT above that — which is what makes the Turing floor
  possible at all.
- The build stops compiling CUDA kernels: no nvcc, no CUDA dev headers, no arch list, no
  fabricated GPU probe, no CPU-dispatch patch, and a fetch instead of a 20-40 minute compile.
- The image can state its llama.cpp version, and `llama-server --version` reports a real
  build number instead of `1`.
- **Accepted negative — staleness is now a deliberate trade.** The image will still often
  carry a llama.cpp well behind upstream, because it rebuilds on `unsloth` releases only.
  This is the intended behaviour, not a defect to be re-litigated: the trigger that matters
  for new model support is an unsloth release, and other bumps go through
  `workflow_dispatch`.
- **Accepted negative — a small freshness regression per build.** The source build cloned
  `master` HEAD, the freshest possible tree; the fork's releases trail upstream by roughly
  hours to two days. Cutting the other way, the fork's builds merge model-support pull
  requests ahead of upstream master, which is the case this image most needs.
- **Accepted negative — downgrade trap on a reused volume.** Once the boot hook has
  replaced `$WORKSPACE/unsloth/llama.cpp` with a symlink to `/opt/llama-cpp`, an **older**
  image tag reusing that same volume finds a dangling symlink: it has no such hook, and
  the workspace sync only copies when the target is absent, so it will not restore a real
  tree. GGUF inference and export stay broken on that instance until
  `$WORKSPACE/unsloth/llama.cpp` is removed. Rolling the image tag back is therefore not
  sufficient on volumes that have already booted this image or later.
- **Accepted negative — supply chain.** We stop compiling readable source and start
  executing a third-party binary as root in every tenant container. The sha256 comes from
  the same release as the artifact, so it protects transit, not trust; the fork publishes no
  build attestations today (checked: none for this bundle, and no attestation step in its
  build workflow). Because CI resolves the release rather than a human, a compromised
  upstream release would be consumed within one poll cycle with no review window. This is
  the accepted cost of option 4's rejection, and the build assertions are what stand in its
  place.
- The shipped binary carries fork patches that never passed upstream review, running in a
  server that parses renter-supplied GGUF files. Each release's marker records the source
  commit, so the delta against upstream is recoverable.
- `sm_103` has no SASS in the bundle despite the manifest claiming it; it reaches kernels by
  JIT from PTX. "Admitted equals supported" was never exactly true at the top end — a GPU
  floor is a lower bound only, and a future major architecture would not be covered by JIT.

## What would reverse this

- The fork stops publishing portable CUDA bundles, retires the `portable` coverage class, or
  renames assets in a way the preflight selection cannot follow. The build fails closed; the
  recovery is the source build this ADR replaces.
- Upstream adds a supported "assume this host at build time" flag, which would make option 2
  honest and cheap and remove the need for our own fetch. This is already listed as a
  reversal condition in ADR 0016.
- The fork begins publishing build attestations, which would materially strengthen the
  provenance story and is worth adopting immediately if it happens.
- Sustained pressure to bump llama.cpp independently of `unsloth` releases (for example a
  run of llama.cpp security fixes) would reopen decision 2 and, with it, the image tag
  scheme.
- For arm64: an aarch64 `torchao` wheel that actually contains compiled extensions, **and**
  either an arm64 CUDA 12 bundle appearing in the fork's releases or this image line moving
  to CUDA 13 with the corresponding driver floor.

## Verification note

ADR 0016 states that the source build "emits SASS-only (no PTX)", and its floor reasoning
rests on that. The studio passes the arch list to CMake without `-real` suffixes, which in
CMake means PTX **and** SASS — so the premise is probably wrong and an uncovered arch on the
current image likely JITs rather than crashing. This does not change any decision here (the
prebuilt bundle's coverage was measured directly), but the claim should be checked against a
shipped image with `cuobjdump --list-ptx` and ADR 0016 corrected if it is wrong, rather than
being carried forward as received wisdom.
