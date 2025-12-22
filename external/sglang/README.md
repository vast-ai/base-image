# SGLang Image

An SGLang image based on the official [lmsysorg/sglang](https://hub.docker.com/r/lmsysorg/sglang) image with Vast.ai tooling added on top. This image includes the Instance Portal, Supervisor process management, and other conveniences from the Vast.ai [base image](https://github.com/vast-ai/base-image).

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../README.md).

## How This Image Works

Unlike derivative images that extend `vastai/base-image`, this image starts from the official SGLang image and adds Vast.ai tooling on top. This approach:

- Preserves the vendor's optimized SGLang installation
- Adds Instance Portal for web-based application management
- Adds Supervisor for process management
- Includes support for `HOTFIX_SCRIPT` and `PROVISIONING_SCRIPT` along with other boot optimizations
- Provides additional system applications useful for working with interactive containers

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/sglang/tags).

## SGLang Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SGLANG_MODEL` | (none) | Model to serve at startup (required) |
| `SGLANG_ARGS` | (none) | Arguments passed to `sglang serve` (see also `/etc/sglang-args.conf` below) |
| `AUTO_PARALLEL` | `true` | Automatically add `--tensor-parallel-size $GPU_COUNT` to `SGLANG_ARGS` |
| `APT_PACKAGES` | (none) | Space-separated list of apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated list of Python packages to install on first boot |

### Complex Arguments

For arguments that are difficult to pass via environment variables (JSON strings, special characters, etc.), write them to `/etc/sglang-args.conf`. The contents of this file are appended to `$SGLANG_ARGS` when launching SGLang.

Example template on start:
```bash
echo '--chat-template chatml' > /etc/sglang-args.conf;
entrypoint.sh
```

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| SGLang API | 8000 | 18000 |
| Jupyter | 8080 | 8080 |

### Interacting with SGLang

**API access via Instance Portal:**
```bash
curl -H "Authorization: Bearer <OPEN_BUTTON_TOKEN>" \
     http://your-instance-ip:8000/v1/models
```

## Building From Source

This image uses a `--build-context` to access files from the base-image repository:

```bash
cd base-image/external/sglang

docker buildx build \
    --platform linux/amd64,linux/arm64 \
    --build-context base_image_source=../.. \
    --build-arg SGLANG_BASE=lmsysorg/sglang:v0.5.6.post2 \
    -t yournamespace/sglang . --push
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `SGLANG_BASE` | `lmsysorg/sglang:v0.5.6.post2` | Official SGLang image to use as the base |
| `VAST_BASE` | `vastai/base-image:stock-ubuntu24.04-py312` | Vast base image (used to copy Caddy binary) |

## Useful Links

- [SGLang Documentation](https://docs.sglang.ai/)
- [Base Image Documentation](../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/external/sglang)
