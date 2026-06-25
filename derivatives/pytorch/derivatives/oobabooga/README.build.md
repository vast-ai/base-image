# Building

This Dockerfile relies on the structure provided by [Vast.Ai Base images](https://github.com/vast-ai/base-image).

See the example below for building instructions.

amd64-only: oobabooga's accelerator wheels (exllamav3, flash_attn, xformers,
llama_cpp_binaries) publish x86_64-only wheels. The torch 2.9.1/cu128 base is
required — the wheels are built `+cu128.torch2.9` and the build proves they import
against it (see the ABI gate in the Dockerfile and
[ADR 0004](../../../../docs/adr/0004-oobabooga-accel-reconciliation-and-ci.md)).
`OOBABOOGA_REF` is kept in lockstep with `ROOT/opt/accel-wheels.txt`.

```bash
docker buildx build \
    --platform linux/amd64 \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312-2026-06-15 \
    --build-arg OOBABOOGA_REF=v4.9 \
    . -t repo/image:tag --push
```
