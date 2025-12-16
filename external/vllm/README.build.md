# Building

This Dockerfile relies on the structure provided by [Vast.Ai Base images](https://github.com/vast-ai/base-image).

See the example below for building instructions.

```bash
docker buildx build \
    --platform linux/amd64 \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.5.1-cuda-12.1.1-py312 \
    --build-arg VLLM_REF=0.8.1 \
    . -t repo/image:tag --push
```
