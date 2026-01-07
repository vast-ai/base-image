# Ostris AI Toolkit Image

An Ostris AI Toolkit image derived from the Vast.ai [PyTorch image](../../README.md), built on the multi-CUDA runtime base for broad GPU compatibility. This image includes the Ostris AI Toolkit pre-installed with a web-based training interface, along with all features from the Vast.ai base image.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../../../README.md).

## How This Image Works

This image is built from the multi-CUDA runtime base image (`vastai/pytorch:*-cuda-runtime-*`), which provides:

- **Forward Compatibility (Datacenter GPUs):** Tesla/datacenter GPUs (V100, A100, H100, etc.) support CUDA forward compatibility, allowing applications compiled against newer CUDA versions to run on older drivers
- **Minor Version Compatibility (Consumer GPUs):** Consumer GPUs (RTX series, etc.) support minor version compatibility within the same CUDA major version

This approach maximizes compatibility across the Vast.ai GPU fleet while maintaining optimal performance.

## GPU Compatibility

| GPU Type | Compatibility Mode | Example GPUs |
|----------|-------------------|--------------|
| **Datacenter** | Forward Compatibility | B200, H200, RTX Pro series, L40S |
| **Consumer** | Minor Version Compatibility | RTX 5090, RTX 4090, RTX A6000 |

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/ostris-ai-toolkit/tags).

## AI Toolkit Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Directory for training data, models, and configurations |
| `AI_TOOLKIT_START_CMD` | `npm run start` | Command to start the AI Toolkit UI |
| `APT_PACKAGES` | (none) | Space-separated list of apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated list of Python packages to install on first boot |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| AI Toolkit UI | 18675 | 8675 |
| Jupyter | 8080 | 8080 |

**Bypassing authentication:** SSH forward to internal port 8675 to access the AI Toolkit directly without authentication.

### Service Management

The AI Toolkit runs as a Supervisor-managed service:

```bash
# Check service status
supervisorctl status ai-toolkit

# Restart the service
supervisorctl restart ai-toolkit

# View service logs
supervisorctl tail -f ai-toolkit
```

## Building From Source

This image requires the PyTorch multi-CUDA runtime base image:

```bash
cd base-image/derivatives/pytorch/derivatives/ostris-ai-toolkit

docker buildx build \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.8.0-cu129-cuda-runtime-12-13-py312 \
    --build-arg AI_TOOLKIT_REF=6870ab4 \
    -t yournamespace/ostris-ai-toolkit .
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `PYTORCH_BASE` | `vastai/2.8.0-cu129-cuda-runtime-12-13-py312` | PyTorch multi-CUDA runtime base image |
| `AI_TOOLKIT_REPO` | `https://github.com/ostris/ai-toolkit` | Git repository URL for the AI Toolkit |
| `AI_TOOLKIT_REF` | `6870ab4` | Git commit, tag, or branch to build from |

### Multi-CUDA Runtime Base

The multi-CUDA runtime base image provides:

- CUDA libraries for multiple CUDA versions (12.x through 13.x)
- Forward compatibility support for datacenter GPUs
- Minor version compatibility for consumer GPUs
- Optimized for the diverse GPU fleet on Vast.ai

This approach ensures the AI Toolkit image works across a wide range of GPU hardware without requiring separate builds for each CUDA version.

## Useful Links

- [Ostris AI Toolkit Documentation](https://github.com/ostris/ai-toolkit)
- [PyTorch Image Documentation](../../README.md)
- [Base Image Documentation](../../../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/ostris-ai-toolkit)
