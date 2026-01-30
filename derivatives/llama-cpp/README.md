# Llama.cpp Image

A Llama.cpp image derived from the Vast.ai [base image](../../README.md). This image includes pre-built [llama.cpp-cuda](https://github.com/ai-dock/llama.cpp-cuda) binaries with all features from the Vast.ai base image.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../README.md).

## How This Image Works

This image extends `vastai/base-image` with pre-built llama.cpp binaries compiled for CUDA. The binaries are sourced from the [ai-dock/llama.cpp-cuda](https://github.com/ai-dock/llama.cpp-cuda) project and installed at build time, so no runtime download is required.

- Pre-built `llama-server` and other llama.cpp tools ready to use
- Automatic model loading via the `LLAMA_MODEL` environment variable
- HuggingFace model support via `llama-server -hf`
- Instance Portal for web-based application management
- Supervisor for process management

### Model Loading

Set the `LLAMA_MODEL` environment variable to a HuggingFace model identifier (e.g., `bartowski/Meta-Llama-3.1-8B-Instruct-GGUF`). The supervisor startup script will launch `llama-server -hf` with the specified model. Additional arguments can be passed via `LLAMA_ARGS`.

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/llama-cpp/tags).

Tags follow the format `<release-tag>-cuda-<version>` (e.g. `b5460-cuda-12.9`). The release tag corresponds to the [ai-dock/llama.cpp-cuda](https://github.com/ai-dock/llama.cpp-cuda/releases) release used to build the image.

## CUDA Compatibility

Images are tagged with the CUDA version of the base image they were built against (e.g. `b5460-cuda-12.9`). This does not mean you need that exact CUDA version on the host.

### Minor Version Compatibility

NVIDIA's [minor version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) guarantees that an application built with any CUDA toolkit release within a major version family will run on a system with a driver from the same major family. In practice:

- A `cuda-12.9` image will run on any machine with a CUDA 12.x driver (driver >= 525).
- A `cuda-13.1` image will run on any machine with a CUDA 13.x driver (driver >= 580).
- The 12.x and 13.x families are separate — a `cuda-13.1` image will not work with a 12.x driver under minor version compatibility alone.

Choose the CUDA variant that matches your driver's major version. Within that family, any minor version will work.

### Forward Compatibility

[Forward compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) allows newer CUDA toolkit versions to run on older drivers. It is only available on **datacenter GPUs** (and select NGC Server Ready RTX cards). All of our images include the CUDA Compatibility Package (`cuda-compat`) to support this.

For example, with forward compatibility a `cuda-12.9` image could run on a datacenter machine with a CUDA 12.1 driver, or a `cuda-13.1` image could run with a CUDA 12.x driver. Consumer GPUs do not support forward compatibility and require a driver that natively supports the image's CUDA version.

## Llama.cpp Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLAMA_MODEL` | (none) | HuggingFace model to load at startup (e.g. `bartowski/Meta-Llama-3.1-8B-Instruct-GGUF`) |
| `LLAMA_ARGS` | `--port 18000` | Extra arguments for `llama-server` |
| `APT_PACKAGES` | (none) | Space-separated list of apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated list of Python packages to install on first boot |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Llama.cpp UI | 8000 | 18000 |
| Jupyter | 8080 | 18080 |

### Interacting with Llama.cpp

**Chat completion (OpenAI-compatible):**
```bash
curl -H "Authorization: Bearer <OPEN_BUTTON_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"model":"model","messages":[{"role":"user","content":"Hello!"}]}' \
     http://your-instance-ip:8000/v1/chat/completions
```

**Text completion:**
```bash
curl -H "Authorization: Bearer <OPEN_BUTTON_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"prompt":"Once upon a time"}' \
     http://your-instance-ip:8000/completion
```

**Health check:**
```bash
curl -H "Authorization: Bearer <OPEN_BUTTON_TOKEN>" \
     http://your-instance-ip:8000/health
```

### Service Management

Llama.cpp runs as a Supervisor-managed service:

```bash
# Check service status
supervisorctl status llama

# Restart the service
supervisorctl restart llama

# View service logs
supervisorctl tail -f llama
```

## Building From Source

```bash
cd base-image/derivatives/llama-cpp

docker buildx build \
    --build-arg BASE_IMAGE=vastai/base-image:cuda-12.9-mini-py312 \
    --build-arg LLAMA_CPP_VERSION=b5460 \
    --build-arg CUDA_VERSION=12.8 \
    -t yournamespace/llama-cpp .
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `BASE_IMAGE` | `vastai/base-image:cuda-12.9-mini-py312` | Vast base image |
| `LLAMA_CPP_VERSION` | (required) | Release tag from [ai-dock/llama.cpp-cuda](https://github.com/ai-dock/llama.cpp-cuda/releases) |
| `CUDA_VERSION` | `12.8` | CUDA version for the pre-built binary package |

## Building with GitHub Actions

You can fork this repository and use the included GitHub Actions workflow to automatically build and push Llama.cpp images to your own DockerHub account.

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

Go to **Actions > Build Llama.cpp Image > Run workflow** and fill in the inputs:

| Input | Description |
|-------|-------------|
| `LLAMA_CPP_VERSION` | A release tag (e.g. `b5460`) or leave empty to simulate a scheduled build |
| `CUDA_VERSION` | CUDA version for the binary package (default: `12.8`) |
| `DOCKERHUB_REPO` | Repository name under your namespace (default: `llama-cpp`) |
| `MULTI_ARCH` | Build for both `amd64` and `arm64` (default: `false`) |
| `CUSTOM_IMAGE_TAG` | Override the version portion of the tag (e.g. `my-custom-build`) |

The workflow pushes images tagged as `<namespace>/<repo>:<release-tag>-cuda-<version>`:

```
yourusername/llama-cpp:b5460-cuda-12.9
```

### Automatic Builds

The workflow includes a weekly schedule (Monday) that automatically builds when new releases are detected on GitHub ([ai-dock/llama.cpp-cuda](https://github.com/ai-dock/llama.cpp-cuda/releases)). GitHub disables scheduled workflows on forks by default -- to enable them, go to the **Actions** tab in your fork and confirm that you want to enable workflows. To disable scheduled builds, edit the `cron` line in `.github/workflows/build-llama-cpp.yml` or remove the `schedule` trigger.

### Customizing the Image

To build a variant with modifications:

1. Edit `derivatives/llama-cpp/Dockerfile` or the files under `derivatives/llama-cpp/ROOT/` in your fork.
2. Trigger a manual build. Your changes will be included in the built image.
3. Supervisor configuration lives in `ROOT/etc/supervisor/conf.d/llama.conf` and the startup script in `ROOT/opt/supervisor-scripts/llama.sh`.

## Useful Links

- [llama.cpp Documentation](https://github.com/ggml-org/llama.cpp)
- [llama.cpp Server Documentation](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
- [ai-dock/llama.cpp-cuda Releases](https://github.com/ai-dock/llama.cpp-cuda/releases)
- [Base Image Documentation](../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/llama-cpp)
