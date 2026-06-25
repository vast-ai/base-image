# Building

This Dockerfile relies on the structure provided by [Vast.Ai Base images](https://github.com/vast-ai/base-image).

See the example below for building instructions.

amd64-only: oobabooga's accelerator wheels (exllamav3, flash_attn, xformers,
llama_cpp_binaries) publish x86_64-only wheels. The torch 2.9.x/cu128 base is
required — the wheels are built `+cu128.torch2.9` and the build proves they import
against it (see the ABI gate in the Dockerfile and
[ADR 0004](../../../../docs/adr/0004-oobabooga-accel-reconciliation-and-ci.md)).
The accel-wheel handling is derived from the chosen `OOBABOOGA_REF`'s requirements
at build time, so CI tracks the latest upstream (CI resolves HEAD of `main`). The
example below pins a tag for a reproducible local build; pass any commit/branch/tag.

```bash
docker buildx build \
    --platform linux/amd64 \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312-2026-06-15 \
    --build-arg OOBABOOGA_REF=v4.9 \
    . -t repo/image:tag --push
```
