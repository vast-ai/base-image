# Building

This Dockerfile relies on the structure provided by [Vast.Ai Base images](https://github.com/vast-ai/base-image).

See the example below for building instructions.

```bash
docker buildx build \
    --platform linux/amd64 \
    --build-arg TORCH_BACKEND=cu128 \
    --build-arg KOHYA_REF=v25.2.1 \
    . -t repo/image:tag --push
```
