# ComfyUI Image

A ComfyUI image derived from the Vast.ai [PyTorch image](../../README.md). This image includes ComfyUI pre-installed with ComfyUI-Manager, along with all features from the Vast.ai base image.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../../../README.md).

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/comfyui/tags).

### CUDA Forward Compatibility

Images tagged `cu130` or above automatically enable CUDA forward compatibility. This allows them to run on datacenter GPUs (e.g., H100, A100, L40S, RTX Pro series) with older driver versions. Consumer GPUs (e.g., RTX 4090, RTX 5090) do not support forward compatibility and require a driver version that natively supports CUDA 13.0 or above.

## ComfyUI Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Directory for ComfyUI installation, models, and outputs |
| `COMFYUI_ARGS` | `--disable-auto-launch --enable-cors-header --port 18188` | Command line arguments for ComfyUI |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| ComfyUI | 8188 | 18188 |
| Jupyter | 8080 | 8080 |

**Bypassing authentication:** SSH forward to internal port 18188 to access ComfyUI directly without authentication.

### Service Management

ComfyUI runs as a Supervisor-managed service:

```bash
# Check service status
supervisorctl status comfyui

# Restart the service
supervisorctl restart comfyui

# View service logs
supervisorctl tail -f comfyui
```

## Building From Source

```bash
cd base-image/derivatives/pytorch/derivatives/comfyui

docker buildx build \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312 \
    --build-arg COMFYUI_REF=v0.8.2 \
    -t yournamespace/comfyui .
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `PYTORCH_BASE` | `vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312` | PyTorch mini base image |
| `COMFYUI_REF` | (required) | Git commit, tag, or branch to build from |

## Useful Links

- [ComfyUI Documentation](https://github.com/Comfy-Org/ComfyUI)
- [ComfyUI-Manager](https://github.com/Comfy-Org/ComfyUI-Manager)
- [PyTorch Image Documentation](../../README.md)
- [Base Image Documentation](../../../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/comfyui)
