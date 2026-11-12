# ADR 0067 — Unsloth Studio installs its own torch on the base image (pilot)

- **Status:** Proposed
- **Date:** 2026-11-12
- **Decision owner:** image maintainers

## Context

App images that need torch are "pytorch-nested": they live under
`derivatives/pytorch/derivatives/`, are built `FROM vastai/pytorch:<dated tag>`, and
treat that base as the single source of truth for torch (a build-time guard fails on any
torch ecosystem drift). The intent was one tested torch layer shared by many images.

Measured on `main`, 2026-11-12:

- **The layer is thin.** `derivatives/pytorch/Dockerfile` is 70 lines: one
  `uv pip install torch==X <companions>` from `torch-companions.json`, then four
  sanity checks.
- **Sharing is low.** 15 images inherit from it across 8 distinct bases (torch 2.7.1,
  2.9.1, 2.10.0 by Python 3.10, 3.11, 3.12). No base serves more than three images, and
  tags are dated, so layers are shared only between images pinned to the same date.
- **The inheritance held an image off its upstream's stack.** unsloth-studio sat on
  torch 2.10.0+cu128 / Python 3.12. Read from `unsloth==2026.11.4`: the Studio
  installer's new-install route on a CUDA 13 machine is torch 2.13 on cu130; upstream
  `install.sh` selects Python 3.13; Unsloth's prebuilt `causal-conv1d` / `mamba-ssm`
  wheels for torch 2.13 and 2.14 exist for CUDA 13 and cp313 only. The image differed on
  all three axes. The drift guard did its job and, with nothing comparing the base
  against upstream, also held the image in place.
- **The companion set was ours, not the app's.** Unsloth pairs torch 2.10.0 with
  xformers 0.0.34 and pairs 0.0.35 with torch 2.11 and later. The image shipped 2.10.0
  with 0.0.35: xformers is not in `torch-companions.json` and was outside the guard.
- **The guard could pass vacuously.** It read versions with
  `pip list ... 2>/dev/null | grep ... || :`. A venv with no `pip` yields an empty
  string before and after, and empty equals empty.

The torch version is an app dependency that the app's upstream declares. The nested
structure treats it as a platform choice that must exist as a published base before an
app can move.

What prompted the check was a segmentation fault training a hybrid Mamba model in
Studio. That fault is NOT explained by the stack: the same script fails on torch
2.13.0+cu130 / Python 3.13 with `AssertionError: User object is no longer alive` from
the same torch module, and passes on either stack with `UNSLOTH_BLOCK_COMPILE=0`. It is
an upstream defect and this ADR does not claim to fix it.

## Options considered

1. **Stay nested, stay on torch 2.10.0.** No work, widest host pool. Rejected: a
   combination upstream no longer installs or builds kernels for.
2. **Stay nested, repin to `vastai/pytorch:2.13.0-cu130-cuda-13.2-mini-py313`.** The
   smallest change that reaches the right stack, and it was written first. Rejected as
   the end state: it fixes this instance and keeps the mechanism that caused it. The
   next Unsloth move again needs a matching published base, the companion set is still
   ours, and xformers is still outside it.
3. **Build on the base image and let the app's own installer choose torch**
   (`--torch-backend=auto`, upstream `install.sh`). Most faithful to upstream.
   Rejected: the build has no GPU, so auto-detection resolves against nothing, and the
   result is whatever upstream published that day with no pin to review.
4. **Build on the base image and install torch from a table kept in this repo** (a
   shared helper around `torch-companions.json`). One implementation, no inheritance.
   Not chosen for this image because upstream already publishes the pairing; it remains
   the likely shape for apps whose upstream publishes none.
5. **Build on the base image and install the torch stack the app publishes** (chosen).

## Decision

unsloth-studio moves to `derivatives/unsloth-studio`, is built
`FROM vastai/base-image:cuda-13.2-mini-py313-<date>`, and installs torch itself through
an extra of the `unsloth` package: `unsloth[cu130onlytorch2130]`, which pins torch
2.13.0, torchvision, torchaudio and xformers together on cu130. `UNSLOTH_TORCH_EXTRA`
in the Dockerfile is the one place the image chooses its torch.

Kept, from the nested design:

- **A drift guard**, now snapshotting what the extra installed (xformers included) and
  failing if `unsloth studio setup` moves it. It reads `uv pip freeze` and fails on an
  empty snapshot.
- **The PyTorch image's own checks on torch**: a CUDA build, on the expected CUDA line,
  that can run a forward pass.
- **The torch test suite and capability files**, taken from `derivatives/pytorch`
  through a named build context instead of being inherited or copied.

This is a pilot on one image. The other 14 nested images, the `pytorch-nested` class,
and ADRs 0013 and 0021 are unchanged. The PyTorch image remains a product in its own
right.

## Binding conditions

- The build's existing assertions pass unchanged on the cuda13 llama.cpp bundle: the
  CUDA backend resolves, and `libggml-cuda.so` carries `sm_75` and `sm_120` SASS.
- Live-GPU QA passes, including the `pytorch.d` suite arriving through the build
  context.
- The pilot is judged on measurements, recorded before any second image moves: image
  size and unique layer size against the nested build, build time, and cold pull time
  on a host that already holds the matching PyTorch layers against one that does not.
- The published tag suffix changes (`-cuda-13.2-py313`), so every template naming the
  image is repointed, with its `cuda_max_good` floor, in the change that publishes it.

## Consequences

- The image is on the torch, CUDA line and Python that Unsloth's installer selects, with
  the companion pairing upstream tests, and can use upstream's prebuilt state-space
  kernels.
- Moving torch is a one-line change in this image and needs no new published base.
- **No torch layer is shared with the PyTorch image.** A host that holds the matching
  PyTorch layers no longer gets them for free; the torch install is a layer unique to
  this image. How often that sharing happened in production was not measured, and it is
  the main thing the pilot has to answer.
- **No separately promoted torch layer under this image.** Its torch is gated by this
  image's own build checks and QA, not by the PyTorch promotion gate (ADR 0021).
- **The host floor rises from CUDA 12.9 to 13.2**, set by the base image line. Measured
  2026-11-12 against the template's other filters: 377 eligible machines at `>= 12.9`,
  369 at `>= 13.0`, 307 at `>= 13.2`, about 19% fewer. This comes from the stack, not
  from leaving the PyTorch base.
- The image is class `derivative` to the linter, so the nested-only rules (L020's
  wiring of the drift guard, `imagegen bump`) no longer apply to it. The guard is kept
  by hand and nothing gates it.
- The build needs `--build-context pytorch=derivatives/pytorch`; without it the build
  fails at the first `COPY --from=pytorch`.
- The provisioning manifest (`unsloth-studio.yaml`) still targets a CUDA 12.9 /
  Python 3.12 base and is NOT changed here.
- Nothing detects the next upstream move. How that is managed is undecided and left
  open.

## What would reverse this

- Cold pull time on warm hosts materially worse than the nested build for this
  template.
- Unsloth dropping or renaming its per-torch extras, leaving no upstream pairing to
  install.
- The cuda13 llama.cpp bundle failing the SASS or link assertions with no upstream fix.
