# Linux Desktop Image

A containerized desktop environment with both low-latency desktop interface by Selkies and VNC support.

This Linux desktop image is built and maintained by Vast.ai and extends the feature-packed Vast.ai [base image](https://github.com/vast-ai/base-image). For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../README.md).

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/linux-desktop/tags).

Tags follow the format: `[cuda-<version>-]ubuntu<version>-<date>`
- CUDA variants: `cuda-13.1-ubuntu24.04-2026-02-01`
- Stock Ubuntu: `ubuntu24.04-2026-02-01`

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `cuda-12.9-ubuntu24.04-2026-02-01`). This does not mean you need that exact CUDA version on the host.

### Minor Version Compatibility

NVIDIA's [minor version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) guarantees that an application built with any CUDA toolkit release within a major version family will run on a system with a driver from the same major family. In practice:

- A `cuda-12.9` image will run on any machine with a CUDA 12.x driver (driver >= 525).
- A `cuda-13.1` image will run on any machine with a CUDA 13.x driver (driver >= 580).
- The 12.x and 13.x families are separate — a `cuda-13.1` image will not work with a 12.x driver under minor version compatibility alone.

Choose the CUDA variant that matches your driver's major version. Within that family, any minor version will work.

### Forward Compatibility

[Forward compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) allows newer CUDA toolkit versions to run on older drivers. It is only available on **datacenter GPUs** (and select NGC Server Ready RTX cards). All of our images include the CUDA Compatibility Package (`cuda-compat`) to support this.

### Stock Ubuntu Images

The `ubuntu<version>-<date>` tags (without CUDA prefix) are stock Ubuntu images without pre-installed CUDA libraries. NVIDIA repositories are configured, so you can install whichever CUDA version you need. Use these when you want full control over your CUDA environment.

## Building From Source

```bash
cd base-image/derivatives/linux-desktop

docker buildx build \
    --build-arg VAST_BASE=vastai/base-image:cuda-12.9-mini-py312 \
    --build-arg BLENDER_VERSION=5.0.1 \
    -t yournamespace/linux-desktop .
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `VAST_BASE` | `vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py312` | Base image to build from |
| `UBUNTU_VERSION` | `24.04` | Ubuntu version for guacamole builder |
| `BLENDER_VERSION` | (auto-detected) | Blender version to install, or empty for latest |

## Building with GitHub Actions

You can fork this repository and use the included GitHub Actions workflow to automatically build and push Linux Desktop images to your own DockerHub account.

### Setup

1. Fork the [base-image](https://github.com/vast-ai/base-image) repository.
2. In your fork, go to **Settings > Secrets and variables > Actions** and add the following repository secrets:

   | Secret | Description |
   |--------|-------------|
   | `DOCKERHUB_USERNAME` | Your DockerHub username |
   | `DOCKERHUB_TOKEN` | A DockerHub [access token](https://docs.docker.com/security/for-developers/access-tokens/) |
   | `DOCKERHUB_NAMESPACE` | DockerHub namespace to push to (usually your username or org) |
   | `SLACK_WEBHOOK_URL` | *(Optional)* Slack incoming webhook for build notifications |

3. Enable Actions in your fork under **Settings > Actions > General**.

### Triggering a Build

Go to **Actions > Build Linux Desktop Image > Run workflow** and fill in the inputs:

| Input | Description |
|-------|-------------|
| `DOCKERHUB_REPO` | Repository name under your namespace (default: `linux-desktop`) |
| `MULTI_ARCH` | Build for both `amd64` and `arm64` (default: `false`) |
| `CUSTOM_IMAGE_TAG` | Override the date portion of the tag |

The workflow builds three images per run:

```
yournamespace/linux-desktop:cuda-13.1-ubuntu24.04-2026-02-01
yournamespace/linux-desktop:cuda-12.9-ubuntu24.04-2026-02-01
yournamespace/linux-desktop:ubuntu24.04-2026-02-01
```

### Automatic Builds

The workflow runs monthly on the 1st of each month. GitHub disables scheduled workflows on forks by default — to enable them, go to the **Actions** tab in your fork and confirm that you want to enable workflows.

## Useful Links

- [Selkies Project](https://github.com/selkies-project)
- [Apache Guacamole](https://guacamole.apache.org/)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/linux-desktop)
