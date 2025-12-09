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

### Automatic CUDA Version Matching

Vast.ai's backend automatically selects the appropriate image variant based on your GPU's maximum supported CUDA version. When you rent a machine:

1. The system detects the GPU's maximum CUDA capability (e.g., CUDA 12.9)
2. It examines available Docker tags for your template
3. It selects the most suitable compatible version (matching or earlier CUDA)

This means you can use a single template, and the system ensures compatibility with any GPU you rent.

## Available Image Variants

We build multiple variants to support different hardware and Python requirements:

### Base Images

| Type | Base Image | Ubuntu | CUDA/ROCm |
|------|-----------|--------|-----------|
| **Stock** | `ubuntu:22.04`, `ubuntu:24.04` | 22.04, 24.04 | None (CPU only) |
| **NVIDIA CUDA** | `nvidia/cuda:*-cudnn-devel-ubuntu*` | 22.04, 24.04 | 11.8, 12.1, 12.4, 12.6, 12.8, 12.9, 13.0 |
| **AMD ROCm** | `rocm/dev-ubuntu-*:6.2.4-complete` | 22.04, 24.04 | ROCm 6.2.4 |

### Python Versions

Each base image variant is available with Python 3.7 through 3.13. The default Python version matches the Ubuntu release:
- Ubuntu 22.04: Python 3.10
- Ubuntu 24.04: Python 3.12

### Tag Format

**Recommended: Use `-auto` tags** for the best defaults:
```
vastai/base-image:cuda-12.8.1-auto
```
The `-auto` suffix automatically selects the recommended Ubuntu and Python versions for that CUDA release.

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
| **CUDA** | Full development toolkit with cuDNN (NVIDIA variants) |
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
| **strace** | System call tracing |

### Instance Portal & Security

The Instance Portal provides a web-based dashboard for managing your instance:

- **Caddy Reverse Proxy**: Automatic TLS for all web applications
- **Authentication**: Bearer token and cookie-based auth via `OPEN_BUTTON_TOKEN`
- **Cloudflare Tunnels**: Share applications without opening ports
- **Centralized Logging**: View logs from `/var/log/portal/` in the web UI

Access the portal by clicking "Open" on your instance card in the Vast.ai console.

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

## Startup Process

When an instance starts, the boot sequence:

1. **Environment Setup** (`10-prep-env.sh`)
   - Exports environment variables to `/etc/environment`
   - Configures runtime settings

2. **First Boot Tasks** (`25-first-boot.sh`)
   - Updates Instance Portal and Vast CLI
   - Runs only on initial startup

3. **Workspace Sync** (`35-sync-home-dirs.sh`, `36-sync-workspace.sh`)
   - Copies default workspace content
   - Optionally syncs environments to persistent storage

4. **Environment Sync** (`37-sync-environment.sh`)
   - Optionally moves Python/Conda environments to workspace volume
   - Enables environment persistence across instance restarts

5. **User Setup** (`45-47-*.sh`)
   - Configures bashrc for both users
   - Propagates SSH keys
   - Sets up git safe directories

6. **TLS Certificate Generation** (`55-tls-cert-gen.sh`)
   - Creates self-signed certificates for secure connections

7. **Supervisor Launch** (`65-supervisor-launch.sh`)
   - Starts all configured services

8. **Provisioning Script** (`75-provisioning-script.sh`)
   - Downloads and executes custom `PROVISIONING_SCRIPT` if set
   - Logs output to `/var/log/portal/provisioning.log`

## Customization

### Using a Provisioning Script

For quick customizations without building a new image, set the `PROVISIONING_SCRIPT` environment variable to a URL:

```bash
PROVISIONING_SCRIPT=https://raw.githubusercontent.com/you/repo/main/setup.sh
```

Example script:
```bash
#!/bin/bash
set -eo pipefail

# Activate the main virtual environment
. /venv/main/bin/activate

# Install packages
pip install torch transformers

# Download models or data
huggingface-cli download meta-llama/Llama-2-7b-hf --local-dir /workspace/models
```

### Building a Derived Image (Recommended)

The best way to create a custom image is to extend our pre-built images from DockerHub. This preserves the layer caching benefits—Vast.ai hosts already have our base layers cached, so only your custom layers need to be downloaded.

**Use `-auto` tags for automatic Python/Ubuntu selection:**

```
vastai/base-image:cuda-12.9.1-auto    # Best config for CUDA 12.9
vastai/base-image:cuda-12.8.1-auto    # Best config for CUDA 12.8
vastai/base-image:cuda-12.6.3-auto    # Best config for CUDA 12.6
vastai/base-image:cuda-12.4.1-auto    # Best config for CUDA 12.4
vastai/base-image:cuda-12.1.1-auto    # Best config for CUDA 12.1
vastai/base-image:cuda-11.8.0-auto    # Best config for CUDA 11.8
```

The `-auto` tags point to the recommended Ubuntu version and Python version for each CUDA release.

**Example Dockerfile:**

```dockerfile
# Extend the pre-built image to preserve layer caching benefits
FROM vastai/base-image:cuda-12.8.1-auto

# Install Python packages into the main virtual environment
RUN . /venv/main/bin/activate && \
    pip install torch torchvision torchaudio transformers accelerate

# Pre-download a model (optional - can also be done via PROVISIONING_SCRIPT)
RUN . /venv/main/bin/activate && \
    huggingface-cli download stabilityai/stable-diffusion-xl-base-1.0 \
        --local-dir /opt/models/sdxl-base

# Add a custom application managed by Supervisor
COPY my-app.conf /etc/supervisor/conf.d/
COPY my-app.sh /opt/supervisor-scripts/
RUN chmod +x /opt/supervisor-scripts/my-app.sh

# Configure Instance Portal to include your app
# Format: hostname:external_port:internal_port:path:name
ENV PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:8080:8080:/:Jupyter|localhost:7860:17860:/:My App"
```

**Example Supervisor config (`my-app.conf`):**

```ini
[program:my-app]
command=/opt/supervisor-scripts/my-app.sh
autostart=true
autorestart=true
stderr_logfile=/var/log/portal/my-app.log
stdout_logfile=/var/log/portal/my-app.log
```

**Example wrapper script (`my-app.sh`):**

```bash
#!/bin/bash
source /venv/main/bin/activate
exec python /opt/my-app/main.py --host localhost --port 17860
```

### Key Paths

| Path | Purpose |
|------|---------|
| `/venv/main/` | Primary Python virtual environment |
| `/workspace/` | Persistent workspace directory |
| `/etc/supervisor/conf.d/` | Supervisor service configurations |
| `/opt/supervisor-scripts/` | Service wrapper scripts |
| `/etc/portal.yaml` | Instance Portal configuration |
| `/var/log/portal/` | Application logs |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PROVISIONING_SCRIPT` | URL to download and execute on startup |
| `PORTAL_CONFIG` | Configure Instance Portal applications |
| `TENSORBOARD_LOG_DIR` | Custom Tensorboard log directory (default: `/workspace`) |
| `CF_TUNNEL_TOKEN` | Enable custom Cloudflare tunnel domains |
| `BOOT_SCRIPT` | URL to custom boot script (replaces default boot) |
| `SERVERLESS` | Set to `true` to skip update checks for faster cold starts |

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

## Template Links

Try the image with these pre-configured templates:

- [Jupyter Launch Mode](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Vast%20Base%20Image)
- [SSH Launch Mode](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Vast%20Base%20Image%20-%20SSH)
- [Args Launch Mode](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Vast%20Base%20Image%20-%20ARGS)
- [ARM64 (Jupyter)](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Vast%20Base%20Image%20-%20ARM64)
- [AMD ROCm (Jupyter)](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=Vast%20Base%20Image%20-%20AMD%20ROCm)

## License

See [LICENSE.md](LICENSE.md) for details.
