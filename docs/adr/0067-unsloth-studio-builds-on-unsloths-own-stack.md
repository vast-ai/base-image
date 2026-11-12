# ADR 0067 — Unsloth Studio builds on the stack Unsloth's installer selects

- **Status:** Proposed
- **Date:** 2026-11-12
- **Decision owner:** image maintainers

## Context

The unsloth-studio image was built on `vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312`:
torch 2.10.0, the cu128 wheel line, Python 3.12. That base was chosen when it matched
Unsloth; Unsloth has since moved and the image did not follow, because the Dockerfile
makes the PyTorch base the single source of truth for torch (it forces
`--torch-backend` and fails the build on any torch ecosystem drift). The guard did its
job and, with nothing comparing the base against upstream, also held the image in place.

Measured on `unsloth==2026.11.4` (read from the installed package, 2026-11-12):

- The package requires `torch>=2.4.0,<2.15.0`, so 2.10.0 is permitted.
- The Studio installer carries a "cu130 torch 2.13 route" for new installs on a
  CUDA 13 machine; the engine requirement sets bundled with Studio pin `torch==2.13.0`
  to match it. Its generic CUDA spec is `torch>=2.4,<2.12.0`.
- Upstream `install.sh` sets `PYTHON_VERSION="3.13"`. Our provisioning manifest
  rewrites that to 3.12.
- Unsloth's own prebuilt `causal-conv1d` and `mamba-ssm` wheels for torch 2.13 and 2.14
  are published for CUDA 13 and cp313 only. On torch 2.13 the pinned upstream sources
  no longer build (`PINNED_SOURCE_BUILD_MAX_TORCH = (2, 12)`), so off cp313 a hybrid
  state-space model has no native kernels at all.
- Unsloth's extras pair torch 2.10.0 with xformers 0.0.34 and pair 0.0.35 with torch
  2.11 and later. The image shipped 2.10.0 with 0.0.35: xformers is outside the drift
  guard, and Unsloth's resolver took the newest.

So the image differed from the stack Unsloth develops and publishes binaries for on
three axes at once: torch minor, CUDA wheel line, and Python version.

What prompted the check was a segmentation fault training a hybrid Mamba model in
Studio. That fault is NOT explained by the stack: the same script fails on torch
2.13.0+cu130 / Python 3.13 with `AssertionError: User object is no longer alive` from
the same torch module, and passes on either stack with `UNSLOTH_BLOCK_COMPILE=0`. It
is an upstream defect and this ADR does not claim to fix it. The stack gap is a
separate finding the investigation surfaced.

## Options considered

1. **Stay on torch 2.10.0+cu128 / py312.** No work and the widest host pool. Rejected:
   it is a combination upstream no longer installs or builds kernels for, the gap
   widens with each Unsloth release, and every upstream-only failure has to be told
   apart from a stack-induced one by hand, as this one was.
2. **torch 2.11.0+cu128 / py312**, the newest the installer's generic CUDA spec
   resolves on the cu128 line. Keeps the CUDA 12.9 host floor. Rejected: it is the
   fallback route, not the one upstream targets; still cp312, so still no prebuilt
   state-space kernels on a path to 2.13.
3. **torch 2.13.0+cu126 / py313.** Matches torch and Python, keeps a CUDA 12 host
   floor. Rejected: the installer's 2.13 route is the cu130 route, and the prebuilt
   kernels are CUDA 13 only, so this is a combination nobody upstream runs.
4. **torch 2.13.0+cu130 / py313 on the CUDA 13.2 base** (chosen).
5. **torch 2.14.1+cu130 / py313.** Inside the package's range and covered by the
   prebuilt kernels. Rejected for now: the installer's new-install route and the
   bundled engine pins name 2.13.0, so 2.14 is permitted rather than selected.

## Decision

Build unsloth-studio on `vastai/pytorch:2.13.0-cu130-cuda-13.2-mini-py313`. With it
move everything that named the old line: `UV_TORCH_BACKEND` and both
`--torch-backend` pins to cu130, the CUDA dev packages to 13.2, the llama.cpp bundle
to `cuda13-portable`, the published tag suffix to `-cuda-13.2-py313`, and the QA
template floor to `cuda_max_good >= 13.2`.

The base stays the single source of truth for torch and the drift guard stays. What
changes is which base.

## Binding conditions

- The build's existing assertions must pass unchanged on the new bundle: the CUDA
  backend resolves, and `libggml-cuda.so` carries both `sm_75` and `sm_120` SASS. If
  the cuda13 bundle lacks either, the template floor or this decision is revisited;
  the assertion is not weakened.
- Live-GPU QA passes on the new image before any promotion.
- The published tag suffix changes, so every template that names the image is
  repointed in the same change that publishes it, with its `cuda_max_good` floor.

## Consequences

- The image matches the torch, CUDA line and Python that Unsloth's installer selects,
  and Studio can use upstream's prebuilt state-space kernels.
- **The host floor rises from CUDA 12.9 to 13.2.** Measured 2026-11-12 against the
  template's other filters: 377 eligible machines at `>= 12.9`, 369 at `>= 13.0`, 307
  at `>= 13.2`, about 19% fewer. The base image line is CUDA 13.2, which is what sets
  the floor; a CUDA 13.0 base would cost about 2%.
- Python 3.13 in `/venv/main`. Anything a user installs into that venv needs a cp313
  wheel.
- The provisioning manifest (`unsloth-studio.yaml`) still targets a CUDA 12.9 /
  Python 3.12 base and is NOT changed here; it installs onto whatever base the user
  picked, and moving it is a separate decision about that base.
- Nothing here detects the next drift. How the base is kept in step with upstream is
  undecided and deliberately left open.

## What would reverse this

- The cuda13 llama.cpp bundle failing the SASS or link assertions with no upstream fix.
- Live QA showing the CUDA 13.2 floor leaves too little capacity for the template.
- Unsloth's installer moving its new-install route to a different torch or Python.
