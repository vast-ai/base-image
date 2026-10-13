# ADR 0054 — Ubuntu 26.04 base images are opt-in, never an auto tag

- **Status:** Accepted
- **Date:** 2026-10-13
- **Decision owner:** Rob Ballantyne
- **Related:** ADR 0019 (auto tags move only on same-run QA), ADR 0024 (driver/CUDA resolution)

## Context

Ubuntu 26.04 LTS is out, and NVIDIA publishes `nvidia/cuda` images for it from CUDA
13.3. Some customers want the newer userland. The hosts are a different matter: 24.04
is the newest host OS in the fleet, and many hosts run older kernels than 26.04 was
built for.

Customer templates mostly use `cuda-X.Y.Z-auto`, which Vast's automatic-tag backend
resolves to the newest image a host's driver supports. Promote derives that tag's name
from `tag_template` by CUDA version alone. A 26.04 config with the same CUDA version as a
24.04 one would compete for the same tag, and whichever the promote's tag ordering
pushed last would win. Customers on that tag would move OS without being told.

A stock 26.04 image was built and booted on two hosts:

- **Kernel 6.8 (a 24.04 host):** the boot, the services and the tools all behaved as
  on 24.04.
- **Kernel 4.15 (an 18.04 host):** boot, services, GPUs, apt, git, uv and Python
  asyncio all worked. GNU `tar` did not. Ubuntu's fix for CVE-2025-45582 (`tar
  1.35+dfsg-4ubuntu0.2` and later) extracts through `openat2`, which arrived in kernel
  5.6, and it has no fallback. On an older kernel every `tar x` fails with ENOSYS, and
  so does creating an archive from an absolute path. Nothing else in the image calls
  `openat2`.
- **Core tools:** 26.04 replaces GNU coreutils with uutils (Rust) by default. Every
  boot stage and supervisor script ran unchanged on it.
- **System Python:** 3.14.
- **NVIDIA's apt key:** the 26.04 repo is signed with a different key (`60DF8A40`)
  from the one the Dockerfile fetched (`3bf863cc`), so the first build failed at the
  repo setup.

## Options considered

1. **Make 26.04 the default for new CUDA versions.** Rejected. Hosts older than 26.04
   would get a userland built for a newer kernel, with no way for a template to opt out.
2. **Give 26.04 its own auto tags under a new name** (for example
   `cuda-X.Y.Z-ubuntu26.04-auto`). Rejected for now. The platform's automatic-tag
   backend is not known to resolve a second family, and nothing asks for one yet.
3. **Leave the matrix unchanged.** Rejected. The image builds and runs; holding it back
   leaves customers who want 26.04 nothing.
4. **Pin GNU coreutils in the 26.04 image.** Not adopted. uutils ran everything in the
   image unchanged, and pinning would be one more divergence from upstream to maintain.
   It stays an option if a uutils difference surfaces.
5. **Replace `tar` with `bsdtar`, or pin a tar without the CVE fix.** Rejected. The
   first changes the tool every script in an image expects; the second gives up a
   security fix. Old kernels are leaving the fleet anyway (see Decision).
6. **Opt-in configs with no auto tag (chosen).**

## Decision

- 26.04 configs are added to `configs/base-image.json` alongside the 24.04 ones and are
  published under their explicit tags only. The first are `stock-26` and `cuda-13.4-26`
  (`nvidia/cuda:13.4.2-cudnn-devel-ubuntu26.04`).
- A config with `"auto_tag": false` takes no auto tag. Every 26.04 CUDA config sets it,
  so `cuda-X.Y.Z-auto` stays on 24.04 for every CUDA version.
- Promote derives `auto_version` once, in `generate-configs`, and every later step
  reads that field, so the opt-out cannot be honoured in one place and missed in
  another. The run fails if two configs claim the same auto tag.
- Like stock, an opted-out config has no QA cell, because promote's QA exists to gate
  auto-tag moves. Its dated tags are promoted without live-GPU QA.
- `default_python` for 26.04 is 3.14, matching its system Python, as 22.04 (3.10) and
  24.04 (3.12) do.
- The Dockerfile installs NVIDIA's apt repo through `cuda-keyring` wherever it sets
  the repo up (the stock configs). The package carries the repo's current signing key,
  which is how NVIDIA's own images do it.
- Hosts with kernels older than 5.6 are out of scope for 26.04. The platform is moving
  hosts to an updated stack, and those that don't upgrade will leave the fleet.

## Binding conditions

- No config that takes an auto tag is 26.04, and no two configs take the same auto
  tag. `test_base_config_table.py` checks both against the table. The same file checks
  that `auto_tag` is only ever the JSON literal `false`, because promote tests
  `.auto_tag != false` and a string `"false"` would leave the tag on.
- The manual tag move (`move-base-auto-tag.yml`, the rollback path) refuses an image
  whose Ubuntu label (`org.opencontainers.image.version`) is not 22.04 or 24.04, or is
  missing. `test_move_auto_tag_behaviour.py` checks both.
- Promote derives the auto version exactly once. `test_base_config_table.py` counts
  the derivations in `promote-base-image.yml`. `test_promote_auto_tag_optout.py` runs
  the real `generate-configs` step against the real table and the real tag-ordering
  step against a fake registry. It checks that an opted-out config sharing a CUDA
  version leaves the auto tag on the 24.04 build and still gets its dated tags.

## Consequences

- Customers can choose 26.04 by explicit tag. No existing template changes image.
- A 26.04 image on a host with a kernel older than 5.6 cannot extract archives with
  `tar`. The README says so.
- 26.04 CUDA images ship without live-GPU QA until something gives them a QA cell.
- 24.04's `tar` (`1.35+dfsg-3ubuntu0.4`) does not call `openat2` today. If Ubuntu
  backports the same CVE fix to 24.04, the 24.04 images inherit the kernel 5.6 floor
  on their next rebuild.
- Switching to `cuda-keyring` changes the repo setup for every stock config (the CUDA
  configs inherit NVIDIA's from their base image). For 22.04 and 24.04 the key
  (`3BF863CC`) and the repo are unchanged; the keyring and source-list file names
  change. The package also installs an apt pin giving NVIDIA's repo priority 600; the
  Dockerfile removes it so stock images resolve packages as before. A downstream
  Dockerfile that adds the same repo with a different `signed-by` path now gets apt's
  "Conflicting values set for option Signed-By"; none in this repo does.
- The Dockerfile checks the fingerprint of the key the keyring package installed
  against the one expected for that release (`...A4B469963BF863CC` for 22.04 and 24.04,
  `...05FBB6DA60DF8A40` for 26.04). If NVIDIA rotates a key, the stock builds fail
  until the expected value is updated.

## What would reverse this

- The fleet's hosts moving to 26.04 would make 26.04 the natural default; that would be
  a new ADR moving the auto tags.
- A uutils incompatibility in a boot stage or a derivative would call for option 4.
- Customers needing 26.04 under an automatic tag would bring option 2 back.
