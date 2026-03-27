# Unsloth Studio Image

An Unsloth Studio image derived from the Vast.ai [PyTorch image](../../README.md). This image includes Unsloth Studio pre-installed with llama.cpp (built with CUDA support), along with all features from the Vast.ai base image.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../../../README.md).

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/unsloth-studio/tags).

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `v0.1.0-cuda-12.9-py312`). This does not mean you need that exact CUDA version on the host.

### Minor Version Compatibility

NVIDIA's [minor version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) guarantees that an application built with any CUDA toolkit release within a major version family will run on a system with a driver from the same major family. In practice:

- A `cuda-12.9` image will run on any machine with a CUDA 12.x driver (driver >= 525).
- A `cuda-13.1` image will run on any machine with a CUDA 13.x driver (driver >= 580).
- The 12.x and 13.x families are separate — a `cuda-13.1` image will not work with a 12.x driver under minor version compatibility alone.

Choose the CUDA variant that matches your driver's major version. Within that family, any minor version will work.

### Forward Compatibility

[Forward compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) allows newer CUDA toolkit versions to run on older drivers. It is only available on **datacenter GPUs** (and select NGC Server Ready RTX cards). All of our images include the CUDA Compatibility Package (`cuda-compat`) to support this.

For example, with forward compatibility a `cuda-12.9` image could run on a datacenter machine with a CUDA 12.1 driver, or a `cuda-13.1` image could run with a CUDA 12.x driver. Consumer GPUs do not support forward compatibility and require a driver that natively supports the image's CUDA version.

## Unsloth Studio Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UNSLOTH_STUDIO_ARGS` | `--host 127.0.0.1 --port 18888` | Command line arguments for Unsloth Studio |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Unsloth Studio | 8888 | 18888 |
| Jupyter | 8080 | 18080 |

**Bypassing authentication:** SSH forward to internal port 18888 to access Unsloth Studio directly without authentication.

### Service Management

Unsloth Studio runs as a Supervisor-managed service:

```bash
# Check service status
supervisorctl status unsloth-studio

# Restart the service
supervisorctl restart unsloth-studio

# View service logs
supervisorctl tail -f unsloth-studio
```

## Build Details

The image uses a single shared venv (`/venv/main`) for both PyTorch and Unsloth Studio. The studio's default behaviour of creating a separate venv at `~/.unsloth/studio/.venv` is bypassed via a symlink to `/venv/main`, avoiding a duplicate PyTorch installation.

The following patches are applied at build time:

- **Torch backend**: `--torch-backend=auto` is replaced with `--torch-backend=cu128` in both `install_python_stack.py` and `setup.sh` to ensure CUDA-enabled PyTorch is installed (no GPU available during Docker build).
- **Venv symlink**: `~/.unsloth/studio/.venv` symlinks to `/venv/main` to prevent venv recreation.
- **CUDA stubs**: `/usr/local/cuda/lib64/stubs` is added to the linker path for llama.cpp compilation without a GPU.

## Building From Source

```bash
cd base-image/derivatives/pytorch/derivatives/unsloth-studio

docker buildx build \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-03-19 \
    -t yournamespace/unsloth-studio .
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `PYTORCH_BASE` | `vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-03-19` | PyTorch mini base image |

## Useful Links

- [Unsloth Documentation](https://github.com/unslothai/unsloth)
- [Unsloth Studio Discussion](https://github.com/unslothai/unsloth/discussions/4370)
- [PyTorch Image Documentation](../../README.md)
- [Base Image Documentation](../../../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/unsloth-studio)
