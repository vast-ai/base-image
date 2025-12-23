# vLLM Image

A vLLM image based on the official [vllm/vllm-openai](https://hub.docker.com/r/vllm/vllm-openai) image with Vast.ai tooling added on top. This image includes the Instance Portal, Supervisor process management, and other conveniences from the Vast.ai [base image](https://github.com/vast-ai/base-image).

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../README.md).

## How This Image Works

Unlike derivative images that extend `vastai/base-image`, this image starts from the official vLLM image and adds Vast.ai tooling on top. This approach:

- Preserves the vendor's optimized vLLM installation
- Adds Instance Portal for web-based application management
- Adds Supervisor for process management
- Includes support for `HOTFIX_SCRIPT` and `PROVISIONING_SCRIPT` along with other boot optimizations
- Provides additional system applications useful for working with interactive containers

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/vllm/tags).

## vLLM Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_MODEL` | (none) | Model to serve at startup |
| `VLLM_ARGS` | (none) | Arguments passed to `vllm serve`. Must be set by the user, for example: `--max-model-len 32768 --download-dir /workspace/models --host 127.0.0.1 --port 18000` (see also `/etc/vllm-args.conf` below). |
| `AUTO_PARALLEL` | `true` | Automatically add `--tensor-parallel-size $GPU_COUNT` to `VLLM_ARGS` |
| `RAY_ARGS` | `--head --port 6379 --dashboard-host 127.0.0.1 --dashboard-port 28265` | Arguments passed to `ray start` |
| `APT_PACKAGES` | (none) | Space-separated list of apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated list of Python packages to install on first boot |

### Complex Arguments

For arguments that are difficult to pass via environment variables (JSON strings, special characters, etc.), write them to `/etc/vllm-args.conf`. The contents of this file are appended to `$VLLM_ARGS` when launching vLLM.

Example template on start:
```bash
echo '--guided-decoding-backend lm-format-enforcer --chat-template-content-format string' > /etc/vllm-args.conf;
entrypoint.sh
```

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| vLLM API | 8000 | 18000 |
| Ray Dashboard | 8265 | 28265 |
| Jupyter | 8080 | 8080 |

### Interacting with vLLM

**Chat from the command line:**
```bash
vllm chat --url http://localhost:18000/v1
```

**API access via Instance Portal:**
```bash
curl -H "Authorization: Bearer <OPEN_BUTTON_TOKEN>" \
     http://your-instance-ip:8000/v1/models
```

## Building From Source

This image uses a `--build-context` to access files from the base-image repository:

```bash
cd base-image/external/vllm

docker buildx build \
    --platform linux/amd64,linux/arm64 \
    --build-context base_image_source=../.. \
    --build-arg VLLM_BASE=vllm/vllm-openai:v0.13.0 \
    -t yournamespace/vllm . --push
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `VLLM_BASE` | `vllm/vllm-openai:v0.13.0` | Official vLLM image to use as the base |
| `VAST_BASE` | `vastai/base-image:stock-ubuntu24.04-py312` | Vast base image (used to copy Caddy binary) |

## Useful Links

- [vLLM Documentation](https://docs.vllm.ai/en/latest)
- [Base Image Documentation](../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/external/vllm)
