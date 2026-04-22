# Wan2GP Image

A Wan2GP image derived from the Vast.ai [PyTorch image](../../README.md). This image includes Wan2GP pre-installed for low-VRAM video generation with Wan-family models, along with all features from the Vast.ai base image.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../../../README.md).

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/wan2gp/tags).

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `<ref>-<date>-cuda-12.9`). This does not mean you need that exact CUDA version on the host.

### Minor Version Compatibility

NVIDIA's [minor version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) guarantees that an application built with any CUDA toolkit release within a major version family will run on a system with a driver from the same major family. In practice:

- A `cuda-12.9` image will run on any machine with a CUDA 12.x driver (driver >= 525).
- A `cuda-13.1` image will run on any machine with a CUDA 13.x driver (driver >= 580).
- The 12.x and 13.x families are separate — a `cuda-13.1` image will not work with a 12.x driver under minor version compatibility alone.

**PTX caveat:** Applications that compile device code to PTX rather than pre-compiled SASS for the target architecture will not work on older drivers within the same major family.

Choose the CUDA variant that matches your driver's major version. Within that family, any minor version will work.

### Forward Compatibility

[Forward compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) allows newer CUDA toolkit versions to run on older drivers. It is only available on **datacenter GPUs** (and select NGC Server Ready RTX cards). All of our images include the CUDA Compatibility Package (`cuda-compat`) to support this.

## Wan2GP Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Directory for models, outputs, and configurations |
| `WAN2GP_PORT` | `17860` | Gradio server port (internal) |
| `WAN2GP_ARGS` | (none) | Extra CLI args appended to `python wgp.py` |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Wan2GP | 17860 | 17860 |
| Jupyter | 8080 | 8080 |

### Service Management

Wan2GP runs as a Supervisor-managed service:

```bash
# Check service status
supervisorctl status wan2gp

# Restart the service
supervisorctl restart wan2gp

# View service logs
supervisorctl tail -f wan2gp
```

## Building From Source

```bash
cd base-image/derivatives/pytorch/derivatives/wan2gp

docker buildx build \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312 \
    --build-arg WAN2GP_REF=<commit-or-tag> \
    -t yournamespace/wan2gp .
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `PYTORCH_BASE` | `vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312-2026-04-15` | PyTorch mini base image |
| `WAN2GP_REPO` | `https://github.com/deepbeepmeep/Wan2GP` | Upstream Wan2GP repository |
| `WAN2GP_REF` | | Git ref (commit/branch/tag) to build |

## Building with GitHub Actions

Fork this repository and use the included GitHub Actions workflow to build and push Wan2GP images to your own DockerHub account.

### Setup

1. Fork the [base-image](https://github.com/vast-ai/base-image) repository.
2. In your fork, add the following repository secrets under **Settings > Secrets and variables > Actions**:

   | Secret | Description |
   |--------|-------------|
   | `DOCKERHUB_USERNAME` | Your DockerHub username |
   | `DOCKERHUB_TOKEN` | A DockerHub [access token](https://docs.docker.com/security/for-developers/access-tokens/) |
   | `DOCKERHUB_NAMESPACE` | DockerHub namespace to push to |
   | `SLACK_WEBHOOK_URL` | *(Optional)* Slack webhook for build notifications |

3. Enable Actions in your fork under **Settings > Actions > General**.

### Triggering a Build

Go to **Actions > Build Wan2GP Image > Run workflow** and fill in the inputs:

| Input | Description |
|-------|-------------|
| `WAN2GP_REF` | Git ref (commit/branch) — leave empty for latest HEAD of main |
| `DOCKERHUB_REPO` | Repository name under your namespace (default: `wan2gp`) |
| `MULTI_ARCH` | Build for both `amd64` and `arm64` |
| `CUSTOM_IMAGE_TAG` | Override the version portion of the tag |

## Licenses

This image ships vendor application(s) under the following license(s):

- **Wan2GP** — Apache-2.0 ([upstream](https://github.com/deepbeepmeep/Wan2GP))

See `/LICENSES.md` in the image for license details and file locations.

## Useful Links

- [Wan2GP GitHub](https://github.com/deepbeepmeep/Wan2GP)
- [PyTorch Image Documentation](../../README.md)
- [Base Image Documentation](../../../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/wan2gp)
