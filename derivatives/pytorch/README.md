# PyTorch Image

A PyTorch image derived from the Vast.ai [base image](https://github.com/vast-ai/base-image). This image includes PyTorch pre-configured in the primary virtual environment at `/venv/main/`, along with all the features and tools from the base image.

For detailed documentation on features, configuration, and usage, see the [base image README](../../README.md).

## Available Tags

We provide auto-selecting tags that always point to the most recent PyTorch release available for each CUDA version:

| Tag | Description |
|-----|-------------|
| `cuda-11.8.0-auto` | Latest PyTorch for CUDA 11.8.0 |
| `cuda-12.1.1-auto` | Latest PyTorch for CUDA 12.1.1 |
| `cuda-12.4.1-auto` | Latest PyTorch for CUDA 12.4.1 |
| `cuda-12.6.3-auto` | Latest PyTorch for CUDA 12.6.3 |
| `cuda-12.8.1-auto` | Latest PyTorch for CUDA 12.8.1 |
| `cuda-12.9.1-auto` | Latest PyTorch for CUDA 12.9.1 |
| `cuda-13.0.2-auto` | Latest PyTorch for CUDA 13.0.2 |

For CUDA 12.4 and newer, images support both AMD64 and ARM64 (Grace) architectures.

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/pytorch/tags).

## Extending This Image

Build your own image on top of the PyTorch image to add custom packages, models, or applications:

```dockerfile
FROM vastai/pytorch:cuda-12.8.1-auto

# Install additional Python packages
RUN . /venv/main/bin/activate && \
    pip install transformers accelerate diffusers

# Download models to a location outside workspace-internal (e.g., /models) for large files,
# then symlink them into /opt/workspace-internal/models for workspace access
RUN . /venv/main/bin/activate && \
    hf download meta-llama/Llama-2-7b-hf --local-dir /models/llama-2-7b

# Create symlink in workspace
RUN mkdir -p /opt/workspace-internal/models && \
    ln -s /models/llama-2-7b /opt/workspace-internal/models/llama-2-7b

# Add custom applications (see base image docs for Supervisor configuration)
COPY my-app.conf /etc/supervisor/conf.d/
COPY my-app.sh /opt/supervisor-scripts/
RUN chmod +x /opt/supervisor-scripts/my-app.sh

# Configure Instance Portal
ENV PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:8080:18080:/:Jupyter|localhost:7860:17860:/:My App"
```

See [Building a Derived Image](../../README.md#building-a-derived-image-recommended) in the base image documentation for complete details on:
- Workspace directory setup
- Adding custom applications with Supervisor
- Instance Portal configuration
- Key paths and environment variables

## Building From Source (Not Recommended)

> **Note:** Building from source creates new image layers that won't be cached on Vast.ai hosts. For most use cases, [extending the pre-built images](#extending-this-image) is faster and more efficient.

If you need to modify the PyTorch image itself, you can build from source:

```bash
git clone https://github.com/vast-ai/base-image
cd base-image/derivatives/pytorch

docker buildx build \
    --build-arg VAST_BASE=vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04 \
    --build-arg PYTORCH_VERSION=2.9.1 \
    --build-arg PYTORCH_BACKEND=cu128 \
    -t my-pytorch-image .
```

The `PYTORCH_BACKEND` argument should match your CUDA version (e.g., `cu118`, `cu121`, `cu124`, `cu128`) or use `rocm` for AMD GPUs.

## Useful Links

- [PyTorch Documentation](https://pytorch.org/)
- [Base Image Documentation](../../README.md)
- [Docker Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch)
