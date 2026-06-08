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
| `cuda-12.9.2-auto` | Latest PyTorch for CUDA 12.9.2 |
| `cuda-13.0.3-auto` | Latest PyTorch for CUDA 13.0.3 |
| `cuda-13.1.2-auto` | Latest PyTorch for CUDA 13.1.2 (cu130 build) |
| `cuda-13.2.1-auto` | Latest PyTorch for CUDA 13.2.1 (cu130 build) |

For CUDA 12.4 and newer, images support both AMD64 and ARM64 (Grace) architectures.

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/pytorch/tags).

## Mini Images

Alongside the full `cuda-<patch>` images we publish **mini** variants for broad CUDA-driver coverage in a smaller image. They are built on the slim **mini** CUDA base images (currently `cuda-12.9-mini` for the CUDA 12 line and `cuda-13.2-mini` for the CUDA 13 line), which carry only a curated CUDA runtime subset. PyTorch wheels bundle their own cuBLAS/cuDNN, so nothing is lost for torch workloads while the image stays small.

The build rides NVIDIA's [minor version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/index.html#minor-version-compatibility): the wheel's CUDA backend sits a minor below the base's CUDA (e.g. a `cu130` build on the `cuda-13.2` base, or `cu126` on `cuda-12.9`), so one image runs across the range of CUDA drivers within that major.

Mini images carry `-mini-` in the tag:

```
<torch>-<backend>-cuda-<minor>-mini-py<NN>-<date>
# e.g. 2.12.0-cu130-cuda-13.2-mini-py312-2026-06-05
```

**When to use:** when you want broad driver compatibility and a smaller image. For the full CUDA library set and an exact driver match, use the corresponding full `cuda-<patch>` image (or a `cuda-<patch>-auto` tag) instead.

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

## Licenses

This image ships vendor application(s) under the following license(s):

- **PyTorch** — BSD-3-Clause ([upstream](https://github.com/pytorch/pytorch))

See `/LICENSES.md` in the image for license details and file locations.

## Useful Links

- [PyTorch Documentation](https://pytorch.org/)
- [Base Image Documentation](../../README.md)
- [Docker Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch)
