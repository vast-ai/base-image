# Open WebUI Image

An Open WebUI + Ollama image based on the official [ghcr.io/open-webui/open-webui](https://github.com/open-webui/open-webui/pkgs/container/open-webui) CUDA image with Vast.ai tooling added on top. This image includes the Instance Portal, Supervisor process management, and other conveniences from the Vast.ai [base image](../../README.md).

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../README.md).

## How This Image Works

Unlike derivative images that extend `vastai/base-image`, this image starts from the official Open WebUI CUDA image and adds Vast.ai tooling on top. This approach:

- Preserves the vendor's optimized Open WebUI installation and frontend
- Bundles Ollama for local model inference out of the box
- Adds Instance Portal for web-based application management
- Adds Supervisor for process management
- Includes support for `HOTFIX_SCRIPT` and `PROVISIONING_SCRIPT` along with other boot optimizations
- Provides additional system applications useful for working with interactive containers

### Boot Sequence

1. Ollama starts and waits for readiness
2. If `OLLAMA_MODEL` is set, the model is pulled automatically
3. Ollama touches `/tmp/.ollama_ready` to signal completion
4. Open WebUI starts only after Ollama is fully ready (server up + model pulled)

### Data Persistence

Open WebUI's database, uploads, and cache are stored in `$WORKSPACE/data`. The upstream image's default data directory (`/app/backend/data`) is pre-seeded into `/opt/workspace-internal/data` at build time and synced to `$WORKSPACE/data` on first boot.

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/openwebui/tags).

Tags follow the format `<version>` (e.g. `v0.8.2`), matching the upstream Open WebUI release version.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | (none) | Model to pull at startup (e.g. `llama3.1:8b`) |
| `OLLAMA_ARGS` | (none) | Extra arguments for `ollama serve` |
| `OLLAMA_HOST` | `0.0.0.0:21434` | Bind address for Ollama server |
| `OLLAMA_MODELS` | `$WORKSPACE/ollama/models` | Model storage path |
| `OPEN_WEBUI_DATA_DIR` | `$WORKSPACE/data` | Open WebUI data directory (database, uploads, cache) |
| `WEBUI_SECRET_KEY` | (auto-generated) | Secret key for Open WebUI session encryption |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | URL Open WebUI uses to reach Ollama |
| `OPENAI_API_KEY` | (none) | OpenAI API key for external model access |
| `OPENAI_API_BASE_URL` | (none) | Custom OpenAI-compatible API base URL |
| `APT_PACKAGES` | (none) | Space-separated list of apt packages to install on first boot |
| `PIP_PACKAGES` | (none) | Space-separated list of Python packages to install on first boot |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Open WebUI | 7500 | 17500 |
| Ollama API | 11434 | 21434 |
| Jupyter | 8080 | 18080 |

### Interacting with the API

**Ollama API access via Instance Portal:**
```bash
curl -H "Authorization: Bearer <OPEN_BUTTON_TOKEN>" \
     http://your-instance-ip:11434/api/tags
```

**Chat completion (OpenAI-compatible via Ollama):**
```bash
curl -H "Authorization: Bearer <OPEN_BUTTON_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"model":"llama3.1:8b","messages":[{"role":"user","content":"Hello!"}]}' \
     http://your-instance-ip:11434/v1/chat/completions
```

## Building From Source

This image uses a `--build-context` to access files from the base-image repository:

```bash
cd base-image/external/openwebui

docker buildx build \
    --platform linux/amd64 \
    --build-context base_image_source=../.. \
    --build-arg OPENWEBUI_BASE=ghcr.io/open-webui/open-webui:v0.8.2-cuda \
    --build-arg OLLAMA_VERSION=0.15.2 \
    -t yournamespace/openwebui . --push
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `OPENWEBUI_BASE` | `ghcr.io/open-webui/open-webui:v0.8.2-cuda` | Official Open WebUI CUDA image to use as the base |
| `OLLAMA_VERSION` | `0.15.2` | Ollama version to install from GitHub releases |
| `VAST_BASE` | `vastai/base-image:stock-ubuntu24.04-py312` | Vast base image (used to copy Caddy binary) |

## Building with GitHub Actions

You can fork this repository and use the included GitHub Actions workflow to automatically build and push Open WebUI images to your own DockerHub account.

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

Go to **Actions > Build Open WebUI Image > Run workflow** and fill in the inputs:

| Input | Description |
|-------|-------------|
| `OPENWEBUI_VERSION` | A release tag (e.g. `v0.8.2`) or leave empty to auto-detect the latest release |
| `OLLAMA_VERSION` | Ollama version (e.g. `0.15.2`) or leave empty to auto-detect |
| `DOCKERHUB_REPO` | Repository name under your namespace (default: `openwebui`) |
| `MULTI_ARCH` | Build for both `amd64` and `arm64` (default: `false`) |
| `CUSTOM_IMAGE_TAG` | Override the version portion of the tag (e.g. `my-custom-build`) |

The workflow pushes images tagged as `<namespace>/<repo>:<version>`:

```
yourusername/openwebui:v0.8.2
```

### Automatic Builds

The workflow includes a 12-hour schedule that automatically builds new Open WebUI releases when detected on GHCR. GitHub disables scheduled workflows on forks by default -- to enable them, go to the **Actions** tab in your fork and confirm that you want to enable workflows. To disable scheduled builds, edit the `cron` line in `.github/workflows/build-openwebui.yml` or remove the `schedule` trigger.

### Customizing the Image

To build a variant with modifications:

1. Edit `external/openwebui/Dockerfile` or the files under `external/openwebui/ROOT/` in your fork.
2. Trigger a manual build. Your changes will be included in the built image.
3. Supervisor configuration lives in `ROOT/etc/supervisor/conf.d/` and the startup scripts in `ROOT/opt/supervisor-scripts/`.

## Useful Links

- [Open WebUI Documentation](https://docs.openwebui.com/)
- [Ollama Documentation](https://github.com/ollama/ollama/blob/main/docs/README.md)
- [Ollama Model Library](https://ollama.com/library)
- [Base Image Documentation](../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/external/openwebui)
