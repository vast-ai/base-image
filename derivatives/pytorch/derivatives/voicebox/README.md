# Voicebox Image

A Voicebox TTS image derived from the Vast.ai [PyTorch image](../../README.md). This image includes the Voicebox API server and web interface pre-installed, along with all features from the Vast.ai base image.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../../../README.md).

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/voicebox/tags).

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `v0.3.1-cuda-12.9-py312`). This does not mean you need that exact CUDA version on the host.

### Minor Version Compatibility

NVIDIA's [minor version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) guarantees that an application built with any CUDA toolkit release within a major version family will run on a system with a driver from the same major family. In practice:

- A `cuda-12.9` image will run on any machine with a CUDA 12.x driver (driver >= 525).
- A `cuda-13.1` image will run on any machine with a CUDA 13.x driver (driver >= 580).
- The 12.x and 13.x families are separate — a `cuda-13.1` image will not work with a 12.x driver under minor version compatibility alone.

Choose the CUDA variant that matches your driver's major version. Within that family, any minor version will work.

### Forward Compatibility

[Forward compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) allows newer CUDA toolkit versions to run on older drivers. It is only available on **datacenter GPUs** (and select NGC Server Ready RTX cards). All of our images include the CUDA Compatibility Package (`cuda-compat`) to support this.

For example, with forward compatibility a `cuda-12.9` image could run on a datacenter machine with a CUDA 12.1 driver, or a `cuda-13.1` image could run with a CUDA 12.x driver. Consumer GPUs do not support forward compatibility and require a driver that natively supports the image's CUDA version.

## Voicebox Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Directory for Voicebox installation and data |
| `VOICEBOX_ARGS` | `--host 127.0.0.1 --port 17493` | Command line arguments for the Voicebox server |
| `VOICEBOX_DATA_DIR` | `${WORKSPACE}/voicebox-data` | Directory for database, profiles, and generated audio |
| `VOICEBOX_CORS_ORIGINS` | (local origins) | Additional CORS origins, comma-separated |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Voicebox | 17493 | 17493 |
| Jupyter | 8080 | 8080 |

**Bypassing authentication:** SSH forward to internal port 17493 to access Voicebox directly without authentication.

### Service Management

Voicebox runs as a Supervisor-managed service:

```bash
# Check service status
supervisorctl status voicebox

# Restart the service
supervisorctl restart voicebox

# View service logs
supervisorctl tail -f voicebox
```

## Building From Source

```bash
cd base-image/derivatives/pytorch/derivatives/voicebox

docker buildx build \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312-2026-03-19 \
    --build-arg VOICEBOX_REF=v0.3.1 \
    -t yournamespace/voicebox .
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `PYTORCH_BASE` | `vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312-2026-03-19` | PyTorch mini base image |
| `VOICEBOX_REF` | (required) | Git commit, tag, or branch to build from |

## Building with GitHub Actions

You can fork this repository and use the included GitHub Actions workflow to automatically build and push Voicebox images to your own DockerHub account.

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

Go to **Actions > Build Voicebox Image > Run workflow** and fill in the inputs:

| Input | Description |
|-------|-------------|
| `VOICEBOX_REF` | A git ref (e.g. `v0.3.1`, `main`, or a commit SHA), or leave empty to auto-detect the latest release |
| `DOCKERHUB_REPO` | Repository name under your namespace (default: `voicebox`) |
| `MULTI_ARCH` | Build for both `amd64` and `arm64` (default: `false`) |
| `CUSTOM_IMAGE_TAG` | Override the version portion of the tag (e.g. `my-custom-build`) |

The workflow builds two images per run with different CUDA versions:

```
yourusername/voicebox:v0.3.1-cuda-12.9-py312
yourusername/voicebox:v0.3.1-cuda-13.1-py312
```

To customize which PyTorch base images are used, modify the `matrix.base_image` array in `.github/workflows/build-voicebox.yml`.

### Automatic Builds

The workflow includes a 12-hour schedule that automatically builds new Voicebox releases when detected on GitHub. GitHub disables scheduled workflows on forks by default — to enable them, go to the **Actions** tab in your fork and confirm that you want to enable workflows. To disable scheduled builds, edit the `cron` line in `.github/workflows/build-voicebox.yml` or remove the `schedule` trigger.

### Customizing the Image

To build a variant with modifications:

1. Edit `derivatives/pytorch/derivatives/voicebox/Dockerfile` or the files under `derivatives/pytorch/derivatives/voicebox/ROOT/` in your fork.
2. Trigger a manual build. Your changes will be included in the built image.
3. Supervisor configuration lives in `ROOT/etc/supervisor/conf.d/voicebox.conf` and the startup script in `ROOT/opt/supervisor-scripts/voicebox.sh`.

## Useful Links

- [Voicebox Documentation](https://github.com/jamiepine/voicebox)
- [PyTorch Image Documentation](../../README.md)
- [Base Image Documentation](../../../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/voicebox)
