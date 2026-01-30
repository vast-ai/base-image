# Ollama Image

An Ollama image based on the official [ollama/ollama](https://hub.docker.com/r/ollama/ollama) image with Vast.ai tooling added on top. This image includes the Instance Portal, Supervisor process management, and other conveniences from the Vast.ai [base image](https://github.com/vast-ai/base-image).

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../README.md).

## How This Image Works

Unlike derivative images that extend `vastai/base-image`, this image starts from the official Ollama image and adds Vast.ai tooling on top. This approach:

- Preserves the vendor's optimized Ollama installation
- Adds Instance Portal for web-based application management
- Adds Supervisor for process management
- Includes support for `HOTFIX_SCRIPT` and `PROVISIONING_SCRIPT` along with other boot optimizations
- Provides additional system applications useful for working with interactive containers

### Model Loading

Ollama requires models to be pulled after the server starts (unlike vLLM/SGLang which take a model path as a CLI argument). Set the `OLLAMA_MODEL` environment variable and a separate supervisor process will automatically pull the model once the server is ready.

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/ollama/tags).

Tags follow the format `<version>` (e.g. `0.15.2`). Unlike vLLM/SGLang, there are no CUDA variant suffixes because Ollama bundles GPU support internally.

## Ollama Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | (none) | Model to pull at startup (e.g. `llama3.1:8b`) |
| `OLLAMA_ARGS` | (none) | Extra arguments for `ollama serve` |
| `OLLAMA_HOST` | `0.0.0.0:21434` | Bind address (set by boot script) |
| `OLLAMA_MODELS` | `$WORKSPACE/ollama/models` | Model storage path (persistent across restarts) |
| `APT_PACKAGES` | (none) | Space-separated list of apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated list of Python packages to install on first boot |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Ollama API | 11434 | 21434 |
| Jupyter | 8080 | 18080 |

### Interacting with Ollama

**Chat from the command line:**
```bash
ollama run llama3.1:8b
```

**API access via Instance Portal:**
```bash
curl -H "Authorization: Bearer <OPEN_BUTTON_TOKEN>" \
     http://your-instance-ip:11434/api/tags
```

**Generate a completion:**
```bash
curl -H "Authorization: Bearer <OPEN_BUTTON_TOKEN>" \
     -d '{"model":"llama3.1:8b","prompt":"Hello!"}' \
     http://your-instance-ip:11434/api/generate
```

**Chat completion (OpenAI-compatible):**
```bash
curl -H "Authorization: Bearer <OPEN_BUTTON_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"model":"llama3.1:8b","messages":[{"role":"user","content":"Hello!"}]}' \
     http://your-instance-ip:11434/v1/chat/completions
```

## Building From Source

This image uses a `--build-context` to access files from the base-image repository:

```bash
cd base-image/external/ollama

docker buildx build \
    --platform linux/amd64,linux/arm64 \
    --build-context base_image_source=../.. \
    --build-arg OLLAMA_BASE=ollama/ollama:0.15.2 \
    -t yournamespace/ollama . --push
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE` | `ollama/ollama:0.15.2` | Official Ollama image to use as the base |
| `VAST_BASE` | `vastai/base-image:stock-ubuntu24.04-py312` | Vast base image (used to copy Caddy binary) |

## Building with GitHub Actions

You can fork this repository and use the included GitHub Actions workflow to automatically build and push Ollama images to your own DockerHub account.

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

Go to **Actions > Build Ollama Image > Run workflow** and fill in the inputs:

| Input | Description |
|-------|-------------|
| `OLLAMA_VERSION` | A release tag (e.g. `0.15.2`) or leave empty to auto-detect the latest release |
| `DOCKERHUB_REPO` | Repository name under your namespace (default: `ollama`) |
| `MULTI_ARCH` | Build for both `amd64` and `arm64` (default: `false`) |
| `CUSTOM_IMAGE_TAG` | Override the version portion of the tag (e.g. `my-custom-build`) |

The workflow pushes images tagged as `<namespace>/<repo>:<version>`:

```
yourusername/ollama:0.15.2
```

### Automatic Builds

The workflow includes a 12-hour schedule that automatically builds new Ollama releases when detected on DockerHub. GitHub disables scheduled workflows on forks by default -- to enable them, go to the **Actions** tab in your fork and confirm that you want to enable workflows. To disable scheduled builds, edit the `cron` line in `.github/workflows/build-ollama.yml` or remove the `schedule` trigger.

### Customizing the Image

To build a variant with modifications:

1. Edit `external/ollama/Dockerfile` or the files under `external/ollama/ROOT/` in your fork.
2. Trigger a manual build. Your changes will be included in the built image.
3. Supervisor configuration lives in `ROOT/etc/supervisor/conf.d/ollama.conf` and the startup script in `ROOT/opt/supervisor-scripts/ollama.sh`.

## Useful Links

- [Ollama Documentation](https://github.com/ollama/ollama/blob/main/docs/README.md)
- [Ollama API Reference](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [Base Image Documentation](../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/external/ollama)
