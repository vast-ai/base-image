# ADR 0057 — Images ship compiled Python bytecode

- **Status:** Accepted
- **Date:** 2026-10-20
- **Decision owner:** Rob Ballantyne

## Context
A fresh container (every new instance, every recycle) compiles much of the Python its app
imports on the first start, then writes the bytecode beside the sources so later restarts skip
it. The images ship only incidental bytecode: uv does not compile on install, app trees in
`/opt/workspace-internal` ship none, and what a venv does ship comes from build-time smoke
imports, so it changes whenever a Dockerfile's checks change.

Measured under runc (fresh-container state restored by deleting bytecode written since PID 1):

| | ComfyUI template | A1111 template |
|---|---|---|
| first start, fresh container | 11.95-12.6 s | 11.2 s |
| warm restart | 10.4-10.5 s | 6.2 s |
| app tree precompiled only | 9.8 s, +7 MiB compressed | 10.7 s, +1 MiB compressed |
| venv + app tree precompiled | 10.5 s, +70 MiB compressed | 6.2 s, +64 MiB compressed |

Where the missing bytecode sits differs per image: ComfyUI's is in the app tree, A1111's in
the venv (4,840 files written on the first start). Precompiling did not slow restarts. Under
gVisor (ADR 0055) file operations are slower, so the first-start cost is expected to be larger;
that is reasoned, not measured, and the decision rests on the runc figures.

External images have the same gap. On the vLLM image the engine's site-packages ship bytecode
for 408 of 30,548 sources, and a container writes thousands of bytecode files into the
`/venv/main` mirror (ADR 0048) at runtime, as real files, which `venv-mirror verify` accepts. The
portal's venv ships no bytecode in any image checked. The engine is imported through
`/venv/main` (its launchers run `/venv/main/bin/python`).

uv can compile on install (`UV_COMPILE_BYTECODE=1`). Measured with uv 0.12.10 from a local wheel,
three repetitions each: a small install took 0.05-0.06 s without it and 0.07-0.09 s with it, and
only the installed package was compiled (two files), whether the venv was partly or fully
compiled already. Older documentation described compiling the whole site-packages each time;
current uv does not.

Two mechanics shape the design. Python's default "timestamp" bytecode records its source's
mtime and size and is discarded on a mismatch. Boot stage 36 copied app trees into `$WORKSPACE`
with `cp -ru`, which gives every copied source a new mtime, so bytecode compiled in
`/opt/workspace-internal` was discarded on first boot.

## Options considered
1. **Compile every venv and app tree in every image, external ones included, and let uv compile
   what each install adds (chosen).** The helper covers everything at build; uv's setting keeps
   later installs (derivative builds, users, the provisioner) compiled at about 0.03 s each.
   Simple, the same everywhere, and nothing depends on bytecode that happens to ship. Costs
   about 65-70 MiB compressed per image (190 MiB on vLLM), some of it unused (ComfyUI's venv).
2. **Compile app trees everywhere, venvs per image where measured to help.** Smallest images
   (ComfyUI +7 MiB), but a per-image judgement that goes stale: ComfyUI's venv only needs no
   compiling because its build happens to import the right modules. Rejected by the owner:
   the gain must not depend on that.
3. **`ENV UV_COMPILE_BYTECODE=1` alone.** First rejected on the belief that uv recompiles the
   whole site-packages after every install; measured, it compiles only what the install adds.
   Adopted as part of option 1, but not enough alone: app trees, pip installs and packages that
   shipped uncompiled are not uv installs in the image's last steps.
7. **Exclude external images.** First chosen, on the belief that compiling into the
   `/venv/main` mirror would break ADR 0048. Measured: Python already writes real bytecode there
   at runtime and `venv-mirror verify` passes. Rejected.
8. **On external images, also compile the engine's own site-packages.** Measured on vLLM: a
   second copy of the same bytecode (186 MiB compressed) that is never imported, since the
   engine runs through `/venv/main`. Rejected.
4. **Ship only what the first start compiles.** Smallest images, but needs a per-image module
   list from a live GPU run, which drifts with every upstream update. Rejected.
5. **Hash-checked bytecode for app trees.** Survives any copy, but every import then reads and
   hashes the source, the file work gVisor is slowest at, for the container's whole life.
   Rejected; preserving timestamps on the copy gives the same robustness for free.
6. **Do nothing.** Leaves 2-5 s (runc) on every fresh container. Rejected.

## Decision
`ROOT/opt/instance-tools/bin/compile-bytecode` compiles, in the image's final RUN before
`env-hash`, every venv's site-packages under `/venv` (and the portal's venv) with that venv's
own interpreter, and every app tree under `/opt/workspace-internal` with `/venv/main`'s.
The base image also sets `ENV UV_COMPILE_BYTECODE=1`, so every uv install compiles what it adds.
Invalidation mode is timestamp, explicitly (SOURCE_DATE_EPOCH would otherwise switch it to
hash checking). Files that fail to compile are tolerated; the build fails if less than 95% of a
tree compiled. It records the trees and interpreters in `/etc/vast-bytecode.tsv`.

Boot stage 36 copies app trees with `--preserve=timestamps`, so their bytecode stays valid in
`$WORKSPACE`. Instance test `base/47-bytecode.sh` checks, per recorded tree, that at least 95%
of sources have valid bytecode where they are imported (the `$WORKSPACE` copy for app trees).

Rollout is in two steps, because derivatives build from a pinned base tag:
1. The base image ships the helper, the uv setting and the stage-36 change, and calls the helper
   itself.
2. Each derivative and external image adds `compile-bytecode &&` to its final RUN when its base
   pin moves to a base that ships the helper (an external image also sets
   `ENV UV_COMPILE_BYTECODE=1`, since it is built from its upstream image, not from the base);
   the generator's templates change then too.

On an external image the helper compiles `/venv/main`, the per-file symlink mirror of the
engine's site-packages (ADR 0048): the bytecode lands as real files in the mirror's `__pycache__`
directories, where Python writes it at runtime anyway. It runs before `venv-mirror verify` in the
final RUN, so the verify proves the mirror still holds. The engine's own site-packages are not
compiled. Measured on vLLM: 100% of every tree compiled in 38 s, and the verify passed.

## Binding conditions
1. Every image runs `compile-bytecode` in its final RUN, before `env-hash` (and on an external
   image before `venv-mirror verify`); only the final RUN may compile. Lint rule L109 enforces
   this on the base image from step 1, and on every image from step 2 (the pin-bump change widens
   the rule), because an image cannot call the helper before its base ships it.
2. Timestamp mode stays explicit; hash-checked or unchecked bytecode is never shipped for app
   trees (unchecked would run stale code after a user edits a source).
3. Stage 36 keeps timestamps on the first-boot copy; `base/47-bytecode.sh` fails if it does not.

## Consequences
- The first start of a fresh container drops to the warm-restart time: 2-5 s saved under runc
  on the measured images, likely more under gVisor.
- Each image grows by roughly 180-200 MiB uncompressed (65-70 MiB to pull; vLLM about 470 MiB,
  190 MiB to pull) and its build by 6-40 s.
- Installs at runtime take about 0.03 s longer and leave their bytecode behind, so the next start
  does not compile them.
- Workspace files copied on first boot keep their build-time mtimes instead of the boot time.
- A derivative on a new base that has not added the call yet still gets its uv installs compiled
  (the base's ENV), but not its app tree or pip installs. `base/47-bytecode.sh` fails if its venv
  coverage drops below 95%; the app tree is caught by L109 once step 2 widens it.
- Existing `$WORKSPACE` volumes keep the trees they already have; nothing changes for them.

## What would reverse this
A measured regression in time to ready (for example pull time on slow hosts outweighing the
first-start gain across the fleet), or a Python change that makes shipped bytecode unusable.
