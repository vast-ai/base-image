# Stable Diffusion Forge Image

A Stable Diffusion WebUI Forge image derived from the Vast.ai [PyTorch image](../../README.md). This image includes Forge pre-installed with xformers and onnxruntime-gpu, along with all features from the Vast.ai base image.

This Dockerfile supports multiple Forge variants: [Forge](https://github.com/lllyasviel/stable-diffusion-webui-forge), [Forge Neo](https://github.com/Haoming02/sd-webui-forge-classic/tree/neo), and [Forge Reforge](https://github.com/Panchovix/stable-diffusion-webui-reForge). The default pre-built images use **Forge Neo**.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../../../README.md).

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/forge/tags).

### CUDA Forward Compatibility

Images tagged `cu130` or above automatically enable CUDA forward compatibility. This allows them to run on datacenter GPUs (e.g., H100, A100, L40S, RTX Pro series) with older driver versions. Consumer GPUs (e.g., RTX 4090, RTX 5090) do not support forward compatibility and require a driver version that natively supports CUDA 13.0 or above.

## Forge Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Directory for Forge installation, models, and outputs |
| `FORGE_ARGS` | `--port 17860` | Command line arguments for Forge |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Forge | 7860 | 17860 |
| Jupyter | 8080 | 8080 |

**Bypassing authentication:** SSH forward to internal port 17860 to access Forge directly without authentication.

### Service Management

Forge runs as a Supervisor-managed service:

```bash
# Check service status
supervisorctl status forge

# Restart the service
supervisorctl restart forge

# View service logs
supervisorctl tail -f forge
```

## Building From Source

This Dockerfile requires specifying the Forge variant via build arguments.

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `PYTORCH_BASE` | `vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py311` | PyTorch mini base image |
| `FORGE_REPO` | (required) | Git repository URL for the Forge variant |
| `FORGE_REF` | (required) | Git commit, tag, or branch to build from |

### Forge (Original)

```bash
docker buildx build \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py311 \
    --build-arg FORGE_REPO=https://github.com/lllyasviel/stable-diffusion-webui-forge \
    --build-arg FORGE_REF=main \
    -t yournamespace/forge .
```

### Forge Neo

```bash
docker buildx build \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py311 \
    --build-arg FORGE_REPO=https://github.com/Haoming02/sd-webui-forge-classic \
    --build-arg FORGE_REF=neo \
    -t yournamespace/forge-neo .
```

### Forge Reforge

```bash
docker buildx build \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py311 \
    --build-arg FORGE_REPO=https://github.com/Panchovix/stable-diffusion-webui-reForge \
    --build-arg FORGE_REF=main \
    -t yournamespace/reforge .
```

## Useful Links

- [Stable Diffusion WebUI Forge](https://github.com/lllyasviel/stable-diffusion-webui-forge)
- [Forge Neo](https://github.com/Haoming02/sd-webui-forge-classic/tree/neo)
- [Forge Reforge](https://github.com/Panchovix/stable-diffusion-webui-reForge)
- [PyTorch Image Documentation](../../README.md)
- [Base Image Documentation](../../../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/sd-forge)
