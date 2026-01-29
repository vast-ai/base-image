# Ostris AI Toolkit Image

An Ostris AI Toolkit image derived from the Vast.ai [PyTorch image](../../README.md). This image includes the Ostris AI Toolkit pre-installed with a web-based training interface, along with all features from the Vast.ai base image.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../../../README.md).

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/ostris-ai-toolkit/tags).

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `ea912d2-cuda-12.9`). This does not mean you need that exact CUDA version on the host.

### Minor Version Compatibility

NVIDIA's [minor version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) guarantees that an application built with any CUDA toolkit release within a major version family will run on a system with a driver from the same major family. In practice:

- A `cuda-12.9` image will run on any machine with a CUDA 12.x driver (driver >= 525).
- A `cuda-13.1` image will run on any machine with a CUDA 13.x driver (driver >= 580).
- The 12.x and 13.x families are separate — a `cuda-13.1` image will not work with a 12.x driver under minor version compatibility alone.

**PTX caveat:** Applications that compile device code to PTX rather than pre-compiled SASS for the target architecture will not work on older drivers within the same major family.

Choose the CUDA variant that matches your driver's major version. Within that family, any minor version will work.

### Forward Compatibility

[Forward compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) allows newer CUDA toolkit versions to run on older drivers. It is only available on **datacenter GPUs** (and select NGC Server Ready RTX cards). All of our images include the CUDA Compatibility Package (`cuda-compat`) to support this.

For example, with forward compatibility a `cuda-12.9` image could run on a datacenter machine with a CUDA 12.1 driver, or a `cuda-13.1` image could run with a CUDA 12.x driver. Consumer GPUs do not support forward compatibility and require a driver that natively supports the image's CUDA version.

## AI Toolkit Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Directory for training data, models, and configurations |
| `AI_TOOLKIT_START_CMD` | `npm run start` | Command to start the AI Toolkit UI |
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

```bash
cd base-image/derivatives/pytorch/derivatives/ostris-ai-toolkit

docker buildx build \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312 \
    --build-arg AI_TOOLKIT_REF=6870ab4 \
    -t yournamespace/ostris-ai-toolkit .
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `PYTORCH_BASE` | `vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312` | PyTorch mini base image |
| `AI_TOOLKIT_REPO` | `https://github.com/ostris/ai-toolkit` | Git repository URL for the AI Toolkit |
| `AI_TOOLKIT_REF` | `6870ab4` | Git commit, tag, or branch to build from |

## Building with GitHub Actions

You can fork this repository and use the included GitHub Actions workflow to automatically build and push Ostris AI Toolkit images to your own DockerHub account.

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

Go to **Actions > Build Ostris AI Toolkit Image > Run workflow** and fill in the inputs:

| Input | Description |
|-------|-------------|
| `AI_TOOLKIT_VERSION` | A DockerHub version (e.g. `0.7.22`), or leave empty to auto-detect the latest release |
| `AI_TOOLKIT_REF` | Git ref override (commit SHA or branch) — leave empty to resolve HEAD of main |
| `DOCKERHUB_REPO` | Repository name under your namespace (default: `ostris-ai-toolkit`) |
| `MULTI_ARCH` | Build for both `amd64` and `arm64` (default: `false`) |
| `CUSTOM_IMAGE_TAG` | Override the version portion of the tag (e.g. `my-custom-build`) |

The workflow builds one image per run:

```
yourusername/ostris-ai-toolkit:ea912d2-cuda-12.9
```

To customize which PyTorch base images are used, modify the `matrix.base_image` array in `.github/workflows/build-ostris-ai-toolkit.yml`.

### Automatic Builds

The workflow includes a 12-hour schedule that automatically builds when new Ostris AI Toolkit releases are detected on DockerHub (`ostris/aitoolkit`). Since this project does not tag GitHub releases, new versions are detected by polling DockerHub for version tags matching `X.Y.Z`. The build always uses the latest commit on the `main` branch of the [ai-toolkit](https://github.com/ostris/ai-toolkit) repository.

GitHub disables scheduled workflows on forks by default — to enable them, go to the **Actions** tab in your fork and confirm that you want to enable workflows. To disable scheduled builds, edit the `cron` line in `.github/workflows/build-ostris-ai-toolkit.yml` or remove the `schedule` trigger.

### Customizing the Image

To build a variant with modifications:

1. Edit `derivatives/pytorch/derivatives/ostris-ai-toolkit/Dockerfile` or the files under `derivatives/pytorch/derivatives/ostris-ai-toolkit/ROOT/` in your fork.
2. Trigger a manual build. Your changes will be included in the built image.
3. Supervisor configuration lives in `ROOT/etc/supervisor/conf.d/ai-toolkit.conf` and the startup script in `ROOT/opt/supervisor-scripts/ai-toolkit.sh`.

## Useful Links

- [Ostris AI Toolkit Documentation](https://github.com/ostris/ai-toolkit)
- [PyTorch Image Documentation](../../README.md)
- [Base Image Documentation](../../../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/ostris-ai-toolkit)
