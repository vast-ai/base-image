# ADR 0033 — llama.cpp binaries come from the unsloth fork, as portable bundles on two CUDA lines

- **Status:** Proposed
- **Date:** 2026-08-25
- **Decision owner:** Rob Ballantyne

## Context

`derivatives/llama-cpp` installs prebuilt binaries from `ai-dock/llama.cpp-cuda`: one
tarball per host CPU arch, CUDA 12.8, built
`75-virtual;80-virtual;86-virtual;89-virtual;90-virtual;100-virtual;120-virtual`.

The model library treats unsloth as the preferred GGUF provider, and unsloth ships
model releases that **only their own llama.cpp build can load**. The fork publishes a
machine-readable `llama-prebuilt-manifest.json` naming the patches it carries; for
`b10472-mix-4b653db` those are two upstream PRs not yet merged (`ggml-org#24423`
DiffusionGemma, `ggml-org#25731` TML Inkling) and three fork-only PRs (`unslothai#70`
kimi-k3 MoonViT-3d vision tower, `unslothai#91` IQ1_XS/XXS/XXXS quant types,
`unslothai#95` a sampling-path change). unsloth publishes GGUFs for exactly those
architectures and quant types. On stock llama.cpp — including the ai-dock build — those
models do not load at all. So the image cannot serve a growing part of the catalogue we
point customers at. That is the problem being decided.

The fork publishes far more variants than ai-dock, which makes this a choice rather than
a substitution. For `b10472-mix-4b653db`:

| bundle | toolkit | declared SMs | size |
|---|---|---|---|
| `x64-cuda12-legacy` | 12.8 | 50, 52, 60, 61 | 37 MB |
| `x64-cuda12-older` | 12.8 | 70, 75, 80, 86, 89 | 212 MB |
| `x64-cuda12-newer` | 12.8 | 86, 89, 90, 100, 103, 120 | 219 MB |
| `x64-cuda12-portable` | 12.8 | 70, 75, 80, 86, 89, 90, 100, 103, 120 | 335 MB |
| `x64-cuda13-older` | 13.3 | 75, 80, 86, 89 | 174 MB |
| `x64-cuda13-newer` | 13.3 | 86, 89, 90, 100, 103, 120 | 218 MB |
| `x64-cuda13-portable` | 13.3 | 75, 80, 86, 89, 90, 100, 103, 120 | 297 MB |
| `arm64-cuda13-portable` | 13.3 | 90, 100, 103, 120, 121 | 180 MB |

**There is no `arm64-cuda12-*` bundle at all.** aarch64 exists only on the CUDA 13 line.

### What the binaries actually contain

The declared `supported_sms` is a publisher summary, not a description of the binary.
Measured with `cuobjdump` on `libggml-cuda.so` from every relevant bundle, 2026-08-25:

| bundle | SASS targets | PTX targets |
|---|---|---|
| ai-dock amd64 / arm64 | **none** | 75, 80, 86, 89, 90, 100, 120a |
| `x64-cuda12-portable` | 70, 75, 80, 86, 89, 90, 100, 120a | identical |
| `x64-cuda13-portable` | 75, 80, 86, 89, 90, 100, 120a | identical |
| `arm64-cuda13-portable` | 90, 100, 120a, 121a | identical |

Five things follow, three of which contradict what this ADR asserted in its first draft:

1. **Both publishers carry a full PTX tail**, one PTX per SASS target (1168 of each in
   `x64-cuda12-portable`). JIT forward-compatibility is preserved.
2. **ai-dock ships zero SASS.** Not "mostly PTX" — none. Every kernel is JIT-compiled by
   the driver at every load, on every GPU. The unsloth bundles add native cubins.
3. **Forward compatibility is unchanged, not narrowed.** Both builds top out at the same
   *generic* virtual target, `compute_100`; ai-dock's `120-virtual` also emits as the
   family-specific `sm_120a`, which cannot JIT to anything but its exact architecture.
4. **`sm_103` is declared but absent** from both x64 bundles. A Blackwell Ultra part is
   served by `sm_100` cubins under minor-version binary compatibility — the classic rule
   ("same major, same or higher minor") still holds on Blackwell; the `a`/`f` suffixes
   extend architecture-specific code across a family rather than replacing it.
5. **The arm64 bundle starts at `sm_90`** where ai-dock's arm64 starts at `sm_75`. This
   is a real coverage loss, not a wash — see Consequences.

Relevant prior decisions: ADR 0024 (driver/CUDA resolution and the forward-compat path in
`ROOT/etc/vast_boot.d/05-configure-cuda.sh`), ADR 0016 (silent CPU fallback as a defect
class), ADR 0005 / 0019 / 0029 / 0031 (the gate conventions this must not violate).

## Options considered

**A. Both `portable` bundles, one per CUDA line. — CHOSEN.**
`cuda-12.9` base + `x64-cuda12-portable` (amd64 only); `cuda-13.2` base +
`x64-cuda13-portable` + `arm64-cuda13-portable` (multi-arch). Two promoted tags, three
build cells. Offers customers a CUDA-line choice, which is the stated intent.

**B. Single `cuda12-portable`, amd64 only.** Widest x64 coverage, simplest.
**Rejected: it drops arm64 entirely**, an unannounced platform regression on Grace
Hopper / Grace Blackwell / Spark hosts.

**C. Single `cuda13-portable`, multi-arch.** One tag, keeps arm64. **Rejected on driver
reach:** CUDA 13 needs a newer minimum driver, and forward-compat libs are
datacenter-only, so a consumer GPU (3090 / 4090 / 5090, a large share of supply) on a
CUDA-12-era driver gets minor-version compatibility within 12.x and nothing across the
major boundary. This strands hosts the current image serves.

**D. `newer` or `older` bundles instead of `portable`.** Saves ~110 MB per line.
**Rejected:** `newer` starts at SM 86, dropping A100; `older` tops out at SM 89, dropping
H100/H200/B200/5090. Neither is a superset of today. The measured PTX tail does not
rescue them — PTX JIT is forward-only, so `compute_86` PTX cannot serve an `sm_80` device.

**E. Ship several bundles and select at boot by detected compute capability.** What the
rank-ordered manifest is built for. **Rejected for now:** multiplies image size and adds a
selection path needing per-GPU-class testing. Its original justification (a bad answer to
the PTX question) evaporated. It stays on the table only as the route to `legacy`
(SM 50-61) coverage, which no chosen bundle has.

**F. Stay on ai-dock.** **Rejected: it cannot load the models this change exists to
serve.**

**G. Build the unsloth source ourselves.** Full control of the SM list. **Rejected on
cost:** a multi-arch CUDA build matrix, hour-plus CI per arch, and every toolchain break,
for a project that already publishes binaries. Remains the fallback if the prebuilt line
stops being usable.

**H. One multi-arch tag with per-arch CUDA lines** — amd64 from `cuda-12.9` +
`x64-cuda12-portable`, arm64 from `cuda-13.2` + `arm64-cuda13-portable`. One promoted
tag, one QA cell pair, no dominated artifact, and no tag choice pushed onto customers.
**This is the strongest rejected option and the argument for it must be recorded
honestly:** for an x86 customer, `x64-cuda13-portable` has *strictly less* SM coverage
(loses SM 70) and *strictly worse* driver reach than `x64-cuda12-portable`. There is no
x86 host on which it is the better artifact; it exists only because it shares a release
with the arm64 asset. Publishing it as a pullable tag re-introduces exactly what Option C
was rejected for, in a form a customer can select by typing a higher version number.
**Rejected by decision:** offering the CUDA-line choice is an explicit product goal, and
the tag-naming rework H needs (`IMAGE_TAG` currently derives from the base's CUDA version,
and `CUDA_VERSION` would become arch-dependent inside the image) is real. The cost of that
rejection is carried in binding conditions 7 and 8 rather than waved away.

## Decision

Option A.

| line | base image | amd64 asset | arm64 asset |
|---|---|---|---|
| CUDA 12 | `cuda-12.9-mini` | `x64-cuda12-portable` | *(none — amd64 only)* |
| CUDA 13 | `cuda-13.2-mini` | `x64-cuda13-portable` | `arm64-cuda13-portable` |

Bundles are flat (no `cuda-X.Y/` subdirectory) with `RUNPATH=$ORIGIN`, so `LLAMA_CPP_DIR`
and `ROOT/etc/vast_boot.d/06-llama-cuda.sh` change. Note that `06-llama-cuda.sh`'s stated
reason for existing — "*Add llama libs after 05-configure-cuda.sh or they will be removed
due to 'cuda' in path*" — **stops applying** under a flat `/opt/llama.cpp`, which contains
no `cuda` substring. Decide whether that file stays or goes; do not leave it writing a
path that no longer exists.

Release detection uses `check-github-release` with a `tag-pattern` restricting resolution
to the `-mix-` line. No change to the action is required — the input already exists.

## Binding conditions

1. ~~Establish whether the bundles carry a PTX tail.~~ **RESOLVED 2026-08-25**: they do,
   on both publishers, one PTX per SASS target. Recorded because the reasoning that
   predicted otherwise was sound and still wrong, which is why it was a condition.

2. **A boot-time compute-capability report — NOT naive membership in `supported_sms`.**
   Such a guard would be wrong in both directions: it would pass `sm_103` (nothing built
   for it) and **fail a future architecture that would JIT fine from `compute_100` PTX**,
   blocking working hardware. Distinguish three states and report rather than gate:
   native SASS; no SASS but a usable PTX ancestor (expect a slow first load); neither.
   Build it from the binary's targets, not the declared list — the two provably disagree.

3. ~~A GPU-offload assertion in the QA suite.~~ **RESOLVED 2026-08-25 and BUILT, ahead of
   this ADR**, because it was a live hole in the *current* ai-dock image rather than a
   consequence of the swap: with `ggml_backend_dl: ON` a failed `dlopen` of
   `libggml-cuda.so` degrades to CPU silently, and a fully CPU-only image passed every
   cell of the gate — serving, contract, and the serverless benchmark — on a 0.5B model.
   Closed by `llama.d/11-llama-offload.sh`, gated by **L076** (both halves: the assertion
   must have a real failure path, and a gating template must require it), with six
   mutation tests. See [docs/invariants.md](../invariants.md).

4. **Build-time checksum verification** against `llama-prebuilt-sha256.json` (per-asset
   `sha256`, verified working). State the limits precisely: it detects transport-level
   damage; it detects **no** change to what the tag serves, malicious or not. Mismatch is
   a hard build failure, verification precedes `tar xf`, and a *missing* entry for our
   asset must fail rather than compare empty-to-empty — the `e3b0c442…` empty-input
   fail-open already in this repo's memory. Worth stating plainly: this is the **first
   integrity check on any third-party fetch in this repo**; the current path is a bare
   `wget` + `tar xf` as root, and `docs/invariants.md` records "no integrity check" as the
   house convention.

5. **A rollback that is a control, not a diff.** "The ai-dock path stays selectable"
   is not a lever while the source repo is a workflow literal with no dispatch input:
   reverting would mean a PR, a merge, a build, a QA cell with redraws, and a production
   approval — hours, in working hours. Add a `LLAMA_BINARY_SOURCE` dispatch input
   (default `unsloth`) wired to both the preflight `repository:` and a Dockerfile
   build-arg. **And record the real first lever:** promotion writes a DockerHub tag;
   customer exposure begins when the Vast recommended template's tag is edited, which is
   manual and outside this repo. Re-pointing that template is the fastest revert and
   belongs as step 1 of a runbook that does not yet exist.

6. **A staleness signal that measures the right thing, on the right clock.** Wall-clock
   release age cannot detect the failure that matters — a fork cutting fresh tags from a
   stale upstream base ships an engine missing every upstream fix, and llama.cpp's fixes
   concentrate in GGUF parsing and server request handling, which is exactly what customer
   traffic touches. The `-mix-` tag encodes the upstream build number, so **upstream lag is
   computable and is the metric**. Two mechanical faults to fix alongside: the action
   computes age from `updated_at` and sorts by it (re-uploading an asset makes a March
   release read as hours old — use `published_at`, already emitted), and
   `RELEASE_AGE_THRESHOLD: 604800` exactly tiles the weekly cron, which is safe only
   against a daily publisher. A skip currently produces **no Slack message at all**.

   **This is not only an unsloth concern, and it is arriving on the current path
   first.** On 2026-08-21 `ggml-org/llama.cpp` cut `v0.2.0` as its first stable semver
   release, and from 08-22 marks every `bNNNN` tag `prerelease=true` — rolling builds
   are now prereleases and `v0.x.y` is stable. ai-dock mirrors upstream's tagging, so
   `v0.2.0-cuda-12.9` is a CORRECT build rather than a defect, and
   `check-github-release` already filters `prerelease != true`. The consequence is
   cadence: once ai-dock adopts the flag the resolver returns only `v0.x.y`, taking the
   effective release rate from daily to occasional — and the 7-day window only ever
   worked because the publisher was daily. A restrictive `^b[0-9]+$` tag-pattern would
   be actively harmful here: it would pin us to a scheme upstream has demoted, and per
   condition 13 we would stop building silently and permanently.

   The same reclassification puts a caveat on this condition's own metric. The `-mix-`
   tag embeds an upstream `bNNNN`, which is now a prerelease counter rather than a
   release number. Lag remains computable today (`upstream_tag: b10472` in the
   manifest), but the anchor is upstream's rolling counter, so if unsloth follows the
   reclassification the calculation needs revisiting rather than silently drifting.

7. ~~Every promoted line gets its own live-GPU QA cell, with a driver floor matching
   that line.~~ **RESOLVED 2026-08-25 and BUILT, ahead of this ADR and still on
   ai-dock**, as a behaviour-preserving refactor with ONE line declared, so adding the
   second is a data change rather than a rewiring. What it replaced: `qa` and
   `qa-serverless` hardcoded `format('{0}-cuda-12.9', …)` with no matrix while
   `merge-manifests` was a matrix over `base_image`, so a second base image would have
   been **promoted on the strength of a QA run against the CUDA 12 artifact**, with
   Slack reporting "live-GPU QA passed" over both. Now: `preflight` expands one `lines`
   declaration into `build-matrix` (per-line × that line's own arches — NOT a
   cross-product, since CUDA 12 has no arm64 asset) and `merge-matrix`; `qa`,
   `qa-serverless` and `merge-manifests` all key on it; each QA cell raises the
   template's floor to its own line via `set_filters: cuda_max_good.gte=<line>` and
   carries a per-cell `evidence_name`. Two dispatch inputs that silently assumed one
   line — `CUSTOM_IMAGE_TAG` (would collide) and `CUDA_VERSION` (would force one
   bundle onto every line, and a CUDA 12 bundle on a CUDA 13 base fails as a silent CPU
   fallback, not a build error) — now fail closed with a message when more than one line
   is declared. **Promotion coupling is all-or-nothing, decided**: `needs.qa.result` is
   the aggregate, so one red line holds every line, including the lines that passed.
   Per-line independence was considered and REJECTED, not deferred. The argument for
   it — these lines carry different binaries, so a CUDA 13 defect is not evidence
   about the CUDA 12 artifact — leaves out that they are not independent products:
   same image, same `ROOT/` overlay, same QA template, same test suite, offered on two
   driver lines under one version string. A red cell far more often means the overlay
   or the gate is wrong than that one binary is, and promoting the other line in that
   state ships a half-verified pair. It also fails closed, where the per-line
   alternative must handle a missing evidence artifact correctly or fail open — and
   `qa-gate.yml` uploads no artifact at all on an ungated scheduled run, the common
   path on this weekly workflow.

8. **The recommended template is the CUDA 13 line.** Decided by the decision owner,
   final, and not to be re-opened on the arguments below — they were raised, answered,
   and are recorded so the answer is discoverable rather than re-derived.

   *Why the objection did not hold.* The concern was Ada (`sm_89`): sampling rentable
   verified offers, 6 of 14 Ada hosts sit on pre-13 drivers, and forward-compat
   libraries are datacenter-only so consumer Ada cannot bridge a major-version gap.
   **The recommended template carries `cuda_max_good >= 13` in its own filters**, so
   those hosts are never offered for it. That makes this a supply-pool question, not a
   broken-image one — a customer launching the recommended template rents from the
   13-capable pool and the image runs natively there. Nothing is stranded; the pool is
   narrower.

   *Two facts worth keeping, because both were measured and both get asked.* On
   ARCHITECTURE the CUDA 13 line is not a compromise: `x64-cuda13-portable` carries
   native SASS **and** PTX for all of `sm_75, 80, 86, 89, 90, 100, 120a` — 146 kernels
   each — so Ada gets a native cubin and never touches the JIT path. The only
   architecture the 13 line lacks against the 12 line is `sm_70` (V100). And the two
   questions are independent: kernels present does not imply a driver that will load
   them, which is why the coverage table alone could not settle this.

   *Consequences of the decision.* The CUDA 12 line remains published and is the wider
   -reach artifact (100% of sampled amd64 supply against 73% at a 13.0 driver floor);
   it is the fallback for anyone who wants V100 or an older driver. arm64 is unaffected
   and in fact better served: all 6 rentable arm64 offers (GB10) report `cuda_max_good`
   >= 13.0, so aarch64 lands on the same recommended line as amd64 rather than needing a
   separate tag — which removes the split this condition previously required.

9. ~~The cuBLAS minor must match the bundle, not the base.~~ **RESOLVED 2026-08-25 by
   measurement: no action needed.** The concern was symbol availability, not the SONAME —
   `libcublas.so.13` loads either way, but a symbol ADDED in 13.3 would not exist in 13.2
   and would surface as an unresolved symbol at `dlopen`, which `ggml_backend_dl` swallows
   into a CPU fallback. Checked directly instead of argued: `libggml-cuda.so` from
   `x64-cuda13-portable` requires **12 cuBLAS symbols and 49 cudart symbols**, and every one
   of them is exported by the `libcublas-13-2` (13.4.1.3) and `cuda-cudart-13-2` (13.2.51)
   packages. llama.cpp uses a small, long-stable slice — `cublasCreate_v2`, `cublasGemmEx`,
   `cublasGemmStridedBatchedEx`, `cublasSetMathMode` and similar — none of it new in 13.3.
   So the base-derived package is sufficient and the `cuda-13.2-mini` base needs no change.
   This matters beyond itself: there is no `cuda-13.3` base published (only 12.9 and 13.2),
   so had the answer gone the other way the fix would have meant installing a non-base CUDA
   minor and keeping it on the loader path against `05-configure-cuda.sh`, which strips every
   line containing `cuda` from every conf file on every boot. **A corollary:
   `06-llama-cuda.sh` can now be deleted rather than repurposed** — its only job was putting
   `/opt/llama.cpp/cuda-${CUDA_VERSION}` on the loader path, a directory the flat bundle
   layout does not have, and `RUNPATH=$ORIGIN` resolves the bundle's own libraries without
   it. The remaining risk is covered: if any of this is wrong the result is a CPU fallback,
   which `llama.d/11-llama-offload` now fails on (condition 3).

   *Original text, kept because the reasoning is still the right check to run on any future
   bundle/base pairing:* **The cuBLAS minor must match the bundle, not the base.** The cuda13 bundles declare
   `toolkit_version: 13.3`; the chosen base is `cuda-13.2-mini` and the Dockerfile derives
   the apt package from the *base* tag, installing `libcublas-13-2`. Building against a
   newer minor and running against an older library is the direction CUDA minor-version
   compatibility does **not** cover, and the failure is an unresolved symbol at `dlopen` —
   which `ggml_backend_dl` swallows into a CPU fallback. Major-match is the wrong test.
   Install the bundle's own minor, or assert with `ldd -r` at build time so the answer is
   a red build. Same check covers the unverified assumption that the 13 base supplies
   `libcudart.so.13` at all: the Dockerfile installs only `libcublas`, and cudart arrives
   transitively today.

10. **Record what was ingested.** A release tag is not a pin — a release asset is a mutable
    blob, and the same URL can serve different bytes later, with the checksum file living
    at the same mutable tag. Record the observed `sha256`, release id, asset id and asset
    timestamp into an OCI label and the capability fragment. Proportionate to the house:
    this repo has no cosign or SBOM machinery, so that would be out of convention.

11. **A determinism check, because the ADR names a `unslothai#95` regression as a reversal
    trigger and nothing could currently observe one.** Greedy decode (`temperature=0`,
    fixed seed, small `max_tokens`), assert byte-identical output across two calls and
    against a recorded golden for the QA model. It does not prove correctness; it converts
    "we would notice nothing" into "we would notice a change".

12. **A backend report on the box.** On-call's question is "is llama.cpp on the GPU?" and
    the image answers nothing. Bake the release manifest in and ship a report printing
    bundle profile, toolkit version, `merged_prs`, the binary's actual SASS/PTX targets,
    the host's compute capability, and whether `libggml-cuda.so` currently loads. That is
    also condition 2's report; write its verdict to a persistent breadcrumb, following the
    marker pattern in `05-configure-cuda.sh`.

13. **Fail loudly when `tag-pattern` matches nothing.** A zero-match resolves to
    `new-release=false` on a schedule, and `notify` is gated on `should-run`, so the
    workflow would stop building **silently and permanently**. `unslothai/llama.cpp`
    currently has ~47 non-`-mix-` releases inside the fetch window, so the `-mix-` line
    occupies roughly half of it. Paginate rather than trusting one page.

Conditions 2 and 7-9 must resolve before this moves from Proposed to Accepted.

## Consequences

**Positive.**
- The image can serve the fork-only architectures and quant types the model library
  already publishes — the point of the change.
- amd64 SM coverage becomes a superset of today's on both lines (SM 70 / V100 gained on
  the 12 line). Note the QA template's `compute_cap.gte: 750` means no cell will ever
  rent an SM 70 box: either lower the floor on the cuda12 cell or stop claiming it.
- **Native SASS where there was none.** ai-dock JIT-compiles every kernel at every load;
  the unsloth bundles ship cubins. First-token latency after a cold start should improve.
  Unmeasured — do not claim a number until one exists.
- The image can state which patches it carries. `merged_prs` is publisher-declared and
  gets the same wording discipline as `supported_sms` on any customer-facing surface —
  it has identical epistemic status and is no more verifiable against the binary.
- Binaries ai-dock does not build: `llama-diffusion-cli`,
  `llama-diffusion-gemma-visual-server`, `llama-gemma3-cli`, `llama-mtmd-cli`, plus
  runtime CPU feature dispatch. The capability fragment advertises none of these and
  should not until each is tested.
- unsloth's `llama-server` does not link `libcuda.so.1` directly, where ai-dock's does —
  so it starts on a host with no driver instead of dying at the loader.

**Accepted negatives.**
- **arm64 loses pre-Hopper GPUs — measured 2026-08-25 as affecting ZERO current supply.**
  The gap is real in the binary and empty in the market: a query of rentable verified
  offers returned 64 amd64 and **5 arm64, every one of them a GB10** (Vast reports
  compute capability 10.0; NVIDIA documents GB10 as 12.1 — the bundle covers `sm_90`,
  `100`, `120a` and `121a`, so it is inside the set under either reading). There is no
  rentable aarch64 supply below `sm_90` to lose. What remains is a forward risk rather
  than a present one: if aarch64 supply later includes a discrete pre-Hopper card — an
  Ampere Altra paired with an A100, say — that host is served today and would not be.
  Re-measure before treating this as closed rather than assuming the market is static.
  The ORIGINAL statement of the negative, which remains the binary-level truth: ai-dock's arm64 covers SM 75-120a; unsloth's starts at
  SM 90. Any aarch64 host with an A100, A10/A30, L4/L40S or T4 is served today and would
  not be after the swap — and the miss mode is silent CPU, not an error. The claim that
  coverage is "a superset on both lines" was false and is withdrawn. **arm64 has never
  been live-GPU gated** (both cells pin `-amd64`), so nothing would catch it.
- **A dominated public tag.** Per Option H, `x64-cuda13-portable` is worse than
  `x64-cuda12-portable` on every x86 host. Condition 8 keeps the fleet off it; nothing
  stops an individual customer choosing it because the number is higher.
- **We take on unmerged code, continuously.** Two of five patches are upstream PRs
  upstream has not accepted, and `unslothai#95` changes the **sampling path for every
  model**. And the mechanism adopted is not "these five patches" — it is a weekly cron
  resolving whatever the fork published most recently, with no diff of `merged_prs`
  between releases. That is a standing delegation, and condition 11 is the only thing
  that would notice a regression.
- **Publisher concentration.** unsloth becomes both the GGUF publisher the model library
  points customers at and the vendor of the binary that parses those GGUFs. Modest — a
  malicious engine already subsumes a malicious weight — but it is a change in blast
  radius and belongs in the record.
- **This is the first unsloth prebuilt binary in the repo.** ADR 0016 is not precedent
  for it: `unsloth-studio` sets `UNSLOTH_LLAMA_FORCE_COMPILE=1` and explicitly *skips*
  the prebuilt path. The honest counterweight is that the same Dockerfile runs
  `uv pip install unsloth` unpinned as root at build time, which is a larger trust grant
  than a hash-verified tarball. So this extends the record, not the org-level trust.
- **~+200 MB compressed per line**, about +6% on a ~3.2 GB pulled image. Measured against
  the template's own `inet_down` floor that is a few seconds; the base and cuBLAS dominate
  the pull, not llama.cpp. The second *tag* costs more than the bytes, by halving
  host-side layer-cache reuse across repeat rentals.
- Two promoted tags where there was one, doubling this image's live-GPU QA cost.

**Correction to the record.** An earlier draft called the `-mix-` line "three releases old
after a five-month gap" and nominated that as the strongest objection. That was wrong — an
artifact of a paginated query reading three releases per page. There are **53 `-mix-`
releases**, near-daily from 2026-06-11 through 2026-08-11, then `b10472` on 08-18 and
silence since. The line is not young. What survives, and matters more operationally, is
the *shape*: near-daily bursts separated by multi-day gaps is precisely the cadence that
defeats a 7-day poll window, which is why condition 6 exists.

## What would reverse this

- **A GPU class arrives the PTX tail cannot JIT for.** `compute_100` PTX covers a future
  architecture in principle; a generation that breaks PTX-level compatibility, or where
  JIT-from-old-PTX is too slow to ship, makes Option E or G cheaper than the exposure.
- **The `-mix-` line stalls.** Condition 6 makes it visible; condition 5 makes reverting
  cheap.
- **Upstream merges the fork's patches.** The fork's value proposition disappears and
  ai-dock — daily, multi-arch on one line — becomes the better source again.
- **A defect traced to `unslothai#95`.** A sampling regression in models unrelated to the
  fork's new architectures would mean a broad risk taken for a narrow gain.
- **Evidence that aarch64 supply includes pre-Hopper GPUs in any volume.** That would
  turn the arm64 negative from accepted to blocking, and push the arm64 variant toward
  Option G or a retained ai-dock arm64.
