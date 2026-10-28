# Unsloth Image

A Vast.ai image for [Unsloth](https://github.com/unslothai/unsloth) training. Includes the
Instance Portal, Supervisor process management, and other conveniences from the Vast.ai
[base image](https://github.com/vast-ai/base-image).

## How it works

An external image: it wraps Unsloth's official `unsloth/unsloth` image, which ships PyTorch,
Unsloth, TRL and vLLM at versions that work together, and grafts the Vast stack on top
(`convert-non-vast-image.sh`). `/venv/main` mirrors the upstream `/opt/unsloth-venv`
(ADR 0048), so Jupyter, `python` in a terminal and `uv pip install` all see the same
packages. The upstream's own launcher (Unsloth Studio, its Jupyter, sshd) is replaced by
the Vast supervisor stack.

vLLM matters for RL: GRPO spends most of each step generating answers, and Unsloth runs
vLLM inside the training process, sharing the GPU's memory. `vastai/unsloth-studio`
does not include it.

The image ships a GRPO recipe in `/workspace/grpo` (synced from `/opt/workspace-internal`
on first boot): `train_grpo.py` and `grpo_quickstart.ipynb`. See
[templates/default/README.md](templates/default/README.md).

## Tags

`<upstream tag>-cuda-<version>`, e.g. `nightly-2026.10.18-cuda-12.8`: the upstream
`unsloth/unsloth` tag it wraps, and that image's CUDA toolkit. amd64 only (the upstream
arm64 build is untested here).

Hosts need a CUDA 13 driver (580+): on a 570 driver the upstream vLLM fails at startup
with "CUDA driver version is insufficient for CUDA runtime version".

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `GRPO_AUTORUN` | `0` | `1` runs `train_grpo.py` once at boot (supervisor program `grpo-autorun`) |
| `GRPO_ARGS` | empty | Extra flags for the autorun |
| `GRPO_*` | see `train_grpo.py --help` | Every script flag, e.g. `GRPO_MODEL`, `GRPO_MAX_STEPS` |
| `HF_TOKEN` | unset | Gated models, `--hf-repo` uploads |

## Tests

`ROOT/opt/instance-tools/tests/unsloth.d/10-unsloth-grpo.sh` runs two GRPO steps with the default model and checks
that a LoRA and `results.json` come out; the QA template requires it to pass.
