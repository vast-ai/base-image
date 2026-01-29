# SGLang Image

An SGLang image based on the official [lmsysorg/sglang](https://hub.docker.com/r/lmsysorg/sglang) image with Vast.ai tooling added on top. This image includes the Instance Portal, Supervisor process management, and other conveniences from the Vast.ai [base image](https://github.com/vast-ai/base-image).

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../README.md).

## How This Image Works

Unlike derivative images that extend `vastai/base-image`, this image starts from the official SGLang image and adds Vast.ai tooling on top. This approach:

- Preserves the vendor's optimized SGLang installation
- Adds Instance Portal for web-based application management
- Adds Supervisor for process management
- Includes support for `HOTFIX_SCRIPT` and `PROVISIONING_SCRIPT` along with other boot optimizations
- Provides additional system applications useful for working with interactive containers

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/sglang/tags).

## SGLang Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SGLANG_MODEL` | (none) | Model to serve at startup (required) |
| `SGLANG_ARGS` | (none) | Arguments passed to `sglang serve` (see also `/etc/sglang-args.conf` below) |
| `AUTO_PARALLEL` | `true` | Automatically add `--tensor-parallel-size $GPU_COUNT` to `SGLANG_ARGS` |
| `APT_PACKAGES` | (none) | Space-separated list of apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated list of Python packages to install on first boot |

### Complex Arguments

For arguments that are difficult to pass via environment variables (JSON strings, special characters, etc.), write them to `/etc/sglang-args.conf`. The contents of this file are appended to `$SGLANG_ARGS` when launching SGLang.

Example template on start:
```bash
echo '--chat-template chatml' > /etc/sglang-args.conf;
entrypoint.sh
```

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| SGLang API | 8000 | 18000 |
| Jupyter | 8080 | 8080 |

### Interacting with SGLang

**API access via Instance Portal:**
```bash
curl -H "Authorization: Bearer <OPEN_BUTTON_TOKEN>" \
     http://your-instance-ip:8000/v1/models
```

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `v0.5.7-cuda-12.9`). This does not mean you need that exact CUDA version on the host.

### Minor Version Compatibility

NVIDIA's [minor version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) guarantees that an application built with any CUDA toolkit release within a major version family will run on a system with a driver from the same major family. In practice:

- A `cuda-12.9` image will run on any machine with a CUDA 12.x driver (driver >= 525).
- A `cuda-13.0` image will run on any machine with a CUDA 13.x driver (driver >= 580).
- The 12.x and 13.x families are separate — a `cuda-13.0` image will not work with a 12.x driver under minor version compatibility alone.

**PTX caveat:** Applications that compile device code to PTX rather than pre-compiled SASS for the target architecture will not work on older drivers within the same major family.

Choose the CUDA variant that matches your driver's major version. Within that family, any minor version will work.

### Forward Compatibility

[Forward compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) is a separate mechanism that allows newer CUDA toolkit versions to run on older drivers across major version boundaries. It is only available on **datacenter GPUs** (and select NGC Server Ready RTX cards). Our images include the CUDA Compatibility Package (`cuda-compat`) to support this.

For example, with forward compatibility a `cuda-13.0` image could run on a machine with a CUDA 12.x datacenter driver. Consumer GPUs do not support this.

## Building From Source

This image uses a `--build-context` to access files from the base-image repository:

```bash
cd base-image/external/sglang

docker buildx build \
    --platform linux/amd64,linux/arm64 \
    --build-context base_image_source=../.. \
    --build-arg SGLANG_BASE=lmsysorg/sglang:v0.5.6.post2 \
    -t yournamespace/sglang . --push
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `SGLANG_BASE` | `lmsysorg/sglang:v0.5.6.post2` | Official SGLang image to use as the base |
| `VAST_BASE` | `vastai/base-image:stock-ubuntu24.04-py312` | Vast base image (used to copy Caddy binary) |

## Building with GitHub Actions

You can fork this repository and use the included GitHub Actions workflow to automatically build and push SGLang images to your own DockerHub account.

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

Go to **Actions > Build SGLang Image > Run workflow** and fill in the inputs:

| Input | Description |
|-------|-------------|
| `SGLANG_VERSION` | A release tag (e.g. `v0.5.7`), `nightly`, or leave empty to auto-detect the latest release |
| `DOCKERHUB_REPO` | Repository name under your namespace (default: `sglang`) |
| `MULTI_ARCH` | Build for both `amd64` and `arm64` (default: `false`) |
| `CUSTOM_IMAGE_TAG` | Override the version portion of the tag (e.g. `my-custom-build`) |

The workflow pushes images tagged as `<namespace>/<repo>:<version>-cuda-<cuda_version>`:

```
yourusername/sglang:v0.5.7-cuda-12.9
yourusername/sglang:v0.5.7-cuda-13.0
```

Nightly builds pull from the upstream `dev` tag and are pushed as:

```
yourusername/sglang:nightly-2025-01-15-cuda-12.9
```

### Automatic Builds

The workflow includes a 12-hour schedule that automatically builds new SGLang releases when detected on DockerHub. GitHub disables scheduled workflows on forks by default — to enable them, go to the **Actions** tab in your fork and confirm that you want to enable workflows. To disable scheduled builds, edit the `cron` line in `.github/workflows/build-sglang.yml` or remove the `schedule` trigger.

### Customizing the Image

To build a variant with modifications:

1. Edit `external/sglang/Dockerfile` or the files under `external/sglang/ROOT/` in your fork.
2. Trigger a manual build. Your changes will be included in the built image.
3. Supervisor configuration lives in `ROOT/etc/supervisor/conf.d/sglang.conf` and the startup script in `ROOT/opt/supervisor-scripts/sglang.sh`.

## Useful Links

- [SGLang Documentation](https://docs.sglang.ai/)
- [Base Image Documentation](../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/external/sglang)
