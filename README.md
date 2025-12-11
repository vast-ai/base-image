# Vast.ai Base Docker Image

A feature-rich base image designed for GPU computing on [Vast.ai](https://vast.ai). This image extends large, commonly-used base images to maximize Docker layer caching benefits, resulting in faster instance startup times.

## Why This Image?

### Optimized for Fast Startup Through Layer Caching

Vast.ai host machines cache commonly-used Docker image layers. By building on top of large, popular base images like `nvidia/cuda` and `rocm/dev-ubuntu`, most of the image content is already present on host machines before you even start your instance.

**How it works:**
- Base images (NVIDIA CUDA, AMD ROCm, Ubuntu) are multi-gigabyte images commonly used across the platform
- These base layers are frequently cached on Vast.ai hosts
- Our image adds development tools, security features, and convenience utilities as additional layers
- When you start an instance, only the smaller top layers need to be downloaded
- Result: Fast startup times despite having a comprehensive development environment

### Automatic CUDA Version Selection

Vast.ai's backend automatically selects the appropriate image variant based on the host machine's maximum supported CUDA version (determined by the installed NVIDIA driver). When you rent a machine:

1. The system detects the host's maximum CUDA capability from its NVIDIA driver (e.g., supports up to CUDA 12.9)
2. It finds the most recently pushed Docker image tag containing a compatible CUDA version
3. It pulls that specific image variant

**Example:** A machine with drivers supporting CUDA 12.8 will pull the `cuda-12.8.1-*` variant, while a newer machine supporting CUDA 12.9 will pull `cuda-12.9.1-*`. This ensures you always get the best compatible version without manual configuration.

## Available Image Variants

We build multiple variants to support different hardware and Python requirements:

### Base Images

| Type | Base Image | Ubuntu | Notes |
|------|-----------|--------|-------|
| **Stock** | `ubuntu:22.04`, `ubuntu:24.04` | 22.04, 24.04 | No CUDA/ROCm libraries, but NVIDIA drivers are still loaded at runtime |
| **NVIDIA CUDA** | `nvidia/cuda:*-cudnn-devel-ubuntu*` | 22.04, 24.04 | Full CUDA toolkit + cuDNN (11.8, 12.1, 12.4, 12.6, 12.8, 12.9, 13.0.1, 13.0.2) |
| **AMD ROCm** | `rocm/dev-ubuntu-*:6.2.4-complete` | 22.04, 24.04 | Complete ROCm 6.2.4 development environment |

**Note:** Stock images can still access NVIDIA GPUs—they simply don't include the heavier CUDA development libraries. Use these when you want a lighter image and will install specific CUDA components yourself.

### Python Versions

Each base image variant is available with Python 3.7 through 3.14. The default Python version matches the Ubuntu release:
- Ubuntu 22.04: Python 3.10
- Ubuntu 24.04: Python 3.12

### Tag Format

**Explicit tags** for specific configurations:
```
vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py310
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ ^^^^^
                  Base image identifier              Python version
```

Tags without the Python suffix use the Ubuntu default (e.g., `cuda-12.8.1-cudnn-devel-ubuntu22.04` uses Python 3.10).

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/base-image/tags).

## Features

### Development Environment

| Category | Tools Included |
|----------|---------------|
| **Python** | Miniforge/Conda, uv package manager, pre-configured `/venv/main` environment |
| **Build Tools** | build-essential, cmake, ninja-build, gdb, libssl-dev |
| **Version Control** | git, git-lfs |
| **Node.js** | NVM with latest LTS version |
| **Editors** | vim, nano |
| **Shell Utilities** | curl, wget, jq, rsync, rclone, zip/unzip, zstd |

### GPU & Compute Support

| Feature | Description |
|---------|-------------|
| **CUDA** | Full development toolkit with cuDNN (NVIDIA CUDA variants) |
| **ROCm** | Complete ROCm development environment (AMD variants) |
| **OpenCL** | Headers, ICD loaders, and runtime for both NVIDIA and AMD |
| **Vulkan** | Runtime and tools |
| **NVIDIA Extras** | OpenGL, video encode/decode libraries (auto-selected for driver compatibility) |
| **Infiniband** | rdma-core, libibverbs, infiniband-diags for high-speed networking |

### System Monitoring

| Tool | Purpose |
|------|---------|
| **htop** | Interactive process viewer |
| **nvtop** | GPU process monitoring |
| **iotop** | I/O usage monitoring |

### Pre-configured Applications

| Application | Description | Default Port |
|-------------|-------------|--------------|
| **Jupyter** | Interactive Python notebooks | 8080 |
| **Syncthing** | Peer-to-peer file synchronization | 8384 |
| **Tensorboard** | ML experiment visualization | 6006 |
| **Cron** | Task scheduling | - |

All applications are managed by [Supervisor](https://supervisord.readthedocs.io/) with configs in `/etc/supervisor/conf.d/`.

### Additional Tools

| Tool | Purpose |
|------|---------|
| **Vast CLI** | Manage Vast.ai instances from the command line |
| **magic-wormhole** | Secure file transfer between machines |
| **Syncthing** | Keep files synchronized across instances |
| **rclone** | Cloud storage management |

### User Configuration

- **Non-root user**: `user` account (UID 1001) with passwordless sudo
- **Shared permissions**: umask 002 for collaborative file access
- **SSH key propagation**: Keys automatically set up for both root and user accounts

## Instance Portal

The Instance Portal is a web-based dashboard for managing applications running on your instance. It provides secure access through TLS, authentication, and Cloudflare tunnels.

Access the portal by clicking "Open" on your instance card in the Vast.ai console. See the [Instance Portal documentation](https://docs.vast.ai/documentation/instances/connect/instance-portal) for complete details.

### PORTAL_CONFIG

The `PORTAL_CONFIG` environment variable defines which applications appear in the Instance Portal. Format:

```
hostname:external_port:internal_port:path:name|hostname:external_port:internal_port:path:name|...
```

| Field | Description |
|-------|-------------|
| `hostname` | Usually `localhost` |
| `external_port` | Port exposed via `-p` flag (must be open in template) |
| `internal_port` | Port where your application listens |
| `path` | URL path for access (usually `/`) |
| `name` | Display name in the portal |

**Example:**
```bash
PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:8080:18080:/:Jupyter|localhost:7860:17860:/:My App"
```

**Port behavior:**
- When `external_port` ≠ `internal_port`: Caddy reverse proxy makes the app available on the external port with TLS and authentication
- When `external_port` = `internal_port`: The application bypasses proxying (direct access), but tunnel links are still created

The configuration is written to `/etc/portal.yaml` on first boot. You can edit this file at runtime and restart Caddy with `supervisorctl restart caddy`.

### Enabling HTTPS

To enable HTTPS for all proxied applications, set:

```bash
ENABLE_HTTPS=true
```

When enabled:
- Caddy serves applications over HTTPS using certificates at `/etc/instance.crt` and `/etc/instance.key`
- Self-signed certificates are generated automatically during boot
- Install the [Vast.ai Jupyter certificate](https://vast.ai/docs/instance-setup/jupyter#installing-the-tls-certificate) locally to avoid browser warnings

### Authentication

Authentication is enabled by default for all proxied ports. Access methods:

1. **Open Button**: Click "Open" on your instance card—automatically sets an auth cookie
2. **Basic Auth**: Username `vastai`, password is your `OPEN_BUTTON_TOKEN`
3. **Bearer Token**: Include `Authorization: Bearer ${OPEN_BUTTON_TOKEN}` header for API access

**Related variables:**
| Variable | Description |
|----------|-------------|
| `ENABLE_AUTH` | Set to `false` to disable authentication (default: `true`) |
| `AUTH_EXCLUDE` | Comma-separated list of external ports to exclude from auth |
| `WEB_USERNAME` | Custom username for basic auth (default: `vastai`) |
| `WEB_PASSWORD` | Custom password (default: auto-generated or `OPEN_BUTTON_TOKEN`) |

### Cloudflare Tunnels

The Instance Portal automatically creates Cloudflare tunnels for your applications, providing URLs like:
```
https://four-random-words.trycloudflare.com
```

For persistent custom domains, set `CF_TUNNEL_TOKEN` to your Cloudflare tunnel token. Note: Each running instance requires a separate tunnel token.

## Startup Configuration

### Entrypoint Arguments

The default boot script (`/opt/instance-tools/bin/boot_default.sh`) accepts these arguments:

| Argument | Description |
|----------|-------------|
| `--no-user-keys` | Skip SSH key propagation to the `user` account |
| `--no-export-env` | Don't export environment variables to `/etc/environment` |
| `--no-cert-gen` | Skip TLS certificate generation |
| `--no-update-portal` | Don't check for Instance Portal updates |
| `--no-update-vast` | Don't check for Vast CLI updates |
| `--no-activate-pyenv` | Don't activate Python environment in shell |
| `--sync-environment` | Sync Python/Conda environments to workspace volume for persistence |
| `--sync-home` | Sync home directories to workspace |
| `--jupyter-override` | Force Jupyter to start even in non-Jupyter launch modes |

**Example** (in Docker run command or template):
```bash
/opt/instance-tools/bin/entrypoint.sh --sync-environment --no-update-portal
```

### Startup Environment Variables

| Variable | Description |
|----------|-------------|
| `BOOT_SCRIPT` | URL to a custom boot script that **replaces** the entire default startup routine |
| `HOTFIX_SCRIPT` | URL to a script that runs very early in boot, before most initialization—use to patch broken containers |
| `PROVISIONING_SCRIPT` | URL to a script that runs after Supervisor starts—use to install packages and configure applications |
| `SERVERLESS` | Set to `true` to skip update checks for faster cold starts |

**Execution order:**
1. `BOOT_SCRIPT` (if set, replaces everything below)
2. `HOTFIX_SCRIPT` (runs first, can modify any part of startup)
3. Normal boot sequence (environment setup, workspace sync, TLS certs, Supervisor)
4. `PROVISIONING_SCRIPT` (runs after Supervisor, installs your customizations)

### Custom Boot Scripts

For derivative images, you can add custom scripts to `/etc/vast_boot.d/` to hook into the boot sequence. Scripts are sourced in alphabetical order by filename, so use numeric prefixes to control ordering:

```
/etc/vast_boot.d/
├── 10-prep-env.sh
├── 25-first-boot.sh
├── 35-sync-home-dirs.sh
├── ...
├── 65-supervisor-launch.sh
├── 75-provisioning-script.sh
├── 80-my-custom-script.sh       # Runs every boot
└── first_boot/
    ├── 05-update-vast.sh        # Runs only on first boot
    └── 20-my-first-boot.sh      # Your first-boot script
```

- **Every boot:** Add scripts directly to `/etc/vast_boot.d/`
- **First boot only:** Add scripts to `/etc/vast_boot.d/first_boot/`

Key ordering points:
- Scripts before `65-*` run before Supervisor starts
- Scripts at `75-*` or later run after Supervisor is running
- First-boot scripts (in `first_boot/`) are sourced at position `25-*`

### Provisioning Script Example

For quick customizations without building a new image:

```bash
#!/bin/bash
set -eo pipefail

# Activate the main virtual environment
. /venv/main/bin/activate

# Install packages
pip install torch transformers

# Download models
hf download meta-llama/Llama-2-7b-hf --local-dir /workspace/models

# Add a new application to Supervisor (see "Adding Custom Applications" for details)
cat > /etc/supervisor/conf.d/my-app.conf << 'EOF'
[program:my-app]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/my-app.sh
autostart=true
autorestart=true
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
redirect_stderr=true
EOF

# Reload Supervisor to pick up new config
supervisorctl reread && supervisorctl update
```

## Building a Derived Image (Recommended)

The best way to create a custom image is to extend our pre-built images from DockerHub. This preserves the layer caching benefits—Vast.ai hosts already have our base layers cached, so only your custom layers need to be downloaded.

### Workspace Directory

The contents of `/opt/workspace-internal/` are copied to `$WORKSPACE` (default `/workspace`) on first boot. This happens because:
- `/workspace` may be a volume mount
- Even if not mounted, copying moves content to the uppermost OverlayFS layer, enabling effective use of Vast's copy tools

**For large models:** Don't place large files directly in `/opt/workspace-internal/`. Instead, store them elsewhere (e.g., `/models/`) and create symlinks:

```dockerfile
# Store large model outside workspace-internal
RUN hf download stabilityai/stable-diffusion-xl-base-1.0 --local-dir /models/sdxl-base

# Create symlink in workspace-internal
RUN mkdir -p /opt/workspace-internal/ComfyUI/models/checkpoints && \
    ln -s /models/sdxl-base /opt/workspace-internal/ComfyUI/models/checkpoints/sdxl-base
```

This avoids duplicating large files when they're copied to the workspace volume.

### Example Dockerfile

```dockerfile
# Extend the pre-built image to preserve layer caching benefits
FROM vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04

# Install Python packages into the main virtual environment
RUN . /venv/main/bin/activate && \
    pip install torch torchvision torchaudio transformers accelerate

# Download a large model to a location outside workspace-internal
RUN . /venv/main/bin/activate && \
    hf download stabilityai/stable-diffusion-xl-base-1.0 --local-dir /models/sdxl-base

# Create symlink so it appears in workspace
RUN mkdir -p /opt/workspace-internal/models && \
    ln -s /models/sdxl-base /opt/workspace-internal/models/sdxl-base

# Add a custom application managed by Supervisor
COPY my-app.conf /etc/supervisor/conf.d/
COPY my-app.sh /opt/supervisor-scripts/
RUN chmod +x /opt/supervisor-scripts/my-app.sh

# Configure Instance Portal to include your app
ENV PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:8080:18080:/:Jupyter|localhost:7860:17860:/:My App"
```

### Adding Custom Applications

Supervisor manages all long-running applications. To add your own:

**1. Create a Supervisor config (`my-app.conf`):**

```ini
[program:my-app]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/my-app.sh
autostart=true
autorestart=true
# IMPORTANT: Log to stdout for Vast.ai logging integration
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
redirect_stderr=true
```

> **Note:** Always configure `stdout_logfile=/dev/stdout` and `redirect_stderr=true`. If you log directly to files, output won't appear in Vast.ai's logging system.

**2. Create a wrapper script (`my-app.sh`):**

```bash
#!/bin/bash

# Import logging utilities for Portal log viewer
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/environment.sh"

# Activate the virtual environment
source /venv/main/bin/activate

# Run your application (bind to localhost, Caddy handles external access)
exec python /opt/my-app/main.py --host localhost --port 17860
```

The logging utilities in `/opt/supervisor-scripts/utils/` handle:
- `logging.sh` - Tees output to `/var/log/portal/${PROC_NAME}.log` for the Portal log viewer
- `environment.sh` - Sets up common environment variables
- `exit_portal.sh` - Checks if the app is configured in portal.yaml before starting

### Key Paths

| Path | Purpose |
|------|---------|
| `/venv/main/` | Primary Python virtual environment (Conda-managed) |
| `/workspace/` | Persistent workspace directory |
| `/opt/workspace-internal/` | Contents copied to `/workspace` on first boot |
| `/etc/supervisor/conf.d/` | Supervisor service configurations |
| `/opt/supervisor-scripts/` | Service wrapper scripts |
| `/opt/supervisor-scripts/utils/` | Shared utilities for logging, environment setup |
| `/etc/portal.yaml` | Instance Portal configuration (generated from `PORTAL_CONFIG`) |
| `/var/log/portal/` | Application logs (viewable in Instance Portal) |
| `/etc/instance.crt`, `/etc/instance.key` | TLS certificates |

## Environment Variables Reference

### Instance Portal

| Variable | Description |
|----------|-------------|
| `PORTAL_CONFIG` | Application configuration (see [PORTAL_CONFIG](#portal_config)) |
| `ENABLE_HTTPS` | Enable HTTPS for proxied applications (default: `false`) |
| `ENABLE_AUTH` | Enable authentication (default: `true`) |
| `AUTH_EXCLUDE` | Comma-separated ports to exclude from authentication |
| `WEB_USERNAME` | Basic auth username (default: `vastai`) |
| `WEB_PASSWORD` | Basic auth password (default: `OPEN_BUTTON_TOKEN`) |
| `CF_TUNNEL_TOKEN` | Cloudflare tunnel token for custom domains |

### Startup

| Variable | Description |
|----------|-------------|
| `BOOT_SCRIPT` | URL to custom boot script (replaces default startup) |
| `HOTFIX_SCRIPT` | URL to early-run patch script |
| `PROVISIONING_SCRIPT` | URL to post-Supervisor setup script |
| `SERVERLESS` | Set to `true` for faster cold starts |

### Applications

| Variable | Description |
|----------|-------------|
| `TENSORBOARD_LOG_DIR` | Tensorboard log directory (default: `/workspace`) |

## Building From Source (Not Recommended)

> **Note:** Building from source creates new image layers that won't be cached on Vast.ai hosts. For most use cases, [extending our pre-built images](#building-a-derived-image-recommended) is faster and more efficient.

If you need to modify the base image itself (not just add layers on top), you can build from source:

```bash
git clone https://github.com/vast-ai/base-image
cd base-image

# Build a specific variant
docker buildx build \
    --build-arg BASE_IMAGE=nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04 \
    --build-arg PYTHON_VERSION=3.11 \
    -t my-base-image .

# Or use the build script for all variants
./build.sh --filter cuda-12.8 --dry-run  # Preview what would be built
./build.sh --filter cuda-12.8            # Build CUDA 12.8 variants
./build.sh --list                        # Show all available configurations
```

## License

See [LICENSE.md](LICENSE.md) for details.
