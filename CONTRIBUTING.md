# Contributing to Vast.ai Templates

This repo builds Docker images and provisioning scripts for [Vast.ai](https://vast.ai) GPU templates. Every image inherits shared infrastructure (Supervisor, Instance Portal, Caddy, Jupyter, workspace sync) from `vastai/base-image`, letting you focus on application-specific setup.

## Template Classes

There are three ways to create a template, listed in order of preference:

| Class | When to use | Stability |
|-------|-------------|-----------|
| **Derivative image** | Default choice for all new templates | High — dependencies baked in, version-controlled, reproducible |
| **External image** | Adapting a large, trusted upstream (vLLM, SGLang, Ollama) | High — multi-stage build wrapping upstream with Vast infrastructure |
| **Provisioning-only** | First drafts and proof-of-concept | Low — runtime installs can break over time. Convert to derivative once validated |

## Repository Structure

```
base-image/
├── ROOT/                          # Filesystem overlay (copied into base image as-is)
│   ├── etc/
│   │   └── vast_boot.d/           # Numbered boot scripts (run in order at startup)
│   └── opt/
│       └── supervisor-scripts/
│           └── utils/             # Shared utilities for supervisor scripts
├── derivatives/                   # Derivative images
│   └── pytorch/                   # vastai/pytorch (FROM vastai/base-image)
│       ├── derivatives/
│       │   └── comfyui/           # vastai/comfyui (FROM vastai/pytorch)
│       │       ├── Dockerfile
│       │       └── ROOT/          # App-specific overlay
│       └── provisioning_scripts/  # Runtime-install scripts for pytorch-based templates
├── external/                      # External images (adapted upstream)
│   ├── ollama/
│   ├── sglang/
│   └── vllm/
├── provisioning_scripts/          # Runtime-install scripts for base-image templates
├── portal-aio/                    # Instance Portal build artifacts
├── tools/                         # Build utilities
└── .github/
    ├── AGENTS.md                  # CI/CD conventions (authoritative reference)
    └── workflows/                 # One build-*.yml per image
```

**Image hierarchy:**

```
vastai/base-image
├── vastai/pytorch
│   ├── vastai/comfyui
│   ├── vastai/ostris
│   └── ...
├── vastai/linux-desktop
├── vastai/llama-cpp
└── vastai/tensorflow
```

External images (vLLM, SGLang, Ollama) start from their upstream base and graft Vast infrastructure on top.

## What the Base Image Provides

Every image inherits:

- **Supervisor** — process manager for all services
- **Instance Portal** — web UI with tabbed access to services
- **Caddy** — reverse proxy / TLS termination
- **Jupyter** — notebook server
- **Python venv** at `/venv/main/`
- **`uv`** — fast Python package installer
- **Workspace sync** — `/opt/workspace-internal/` syncs to `$WORKSPACE` (default `/workspace`) on volumes
- **Boot sequence** — numbered scripts in `ROOT/etc/vast_boot.d/`

### Key Environment Variables

| Variable | Purpose |
|----------|---------|
| `WORKSPACE` | User workspace directory (default `/workspace`) |
| `DATA_DIRECTORY` | Alias for `WORKSPACE` |
| `PORTAL_CONFIG` | Defines Instance Portal tabs (see [Portal Integration](#portal-integration)) |
| `PROVISIONING_SCRIPT` | URL of a provisioning script to run at boot |
| `SERVERLESS` | Set to `"true"` to skip portal-dependent services |
| `PROC_NAME` | Set by supervisor — the `[program:name]` value |

### Boot Sequence

Scripts in `ROOT/etc/vast_boot.d/` run in numeric order:

```
05-configure-cuda.sh    # CUDA setup
10-prep-env.sh          # Environment preparation
15-hotfix.sh            # Runtime hotfixes
25-first-boot.sh        # First boot tasks
35-sync-home-dirs.sh    # Home directory sync
36-sync-workspace.sh    # Workspace sync (workspace-internal → $WORKSPACE)
37-sync-environment.sh  # Environment sync
45-user-write-bashrc.sh # User shell config
46-user-propagate-ssh-keys.sh
47-user-git-safe-dirs.sh
48-venv-backup.sh       # Venv state backup
55-tls-cert-gen.sh      # TLS certificate generation
65-supervisor-launch.sh # Start supervisor
75-provisioning-script.sh # Run PROVISIONING_SCRIPT (if set)
95-supervisor-wait.sh   # Wait for supervisor readiness
```

### The `/.provisioning` Marker

During boot, the file `/.provisioning` exists. Supervisor scripts should wait for it to be removed before starting their application:

```bash
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed"
    sleep 5
done
```

---

## Creating a Derivative Image

**This is the preferred approach for all new templates.**

Derivative images use `FROM vastai/pytorch:<tag>` (or `FROM vastai/base-image:<tag>`) and bake all dependencies into the image at build time.

### Dockerfile Pattern

Reference: `derivatives/pytorch/derivatives/comfyui/Dockerfile`

```dockerfile
ARG PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="<App> image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

RUN \
    set -euo pipefail && \
    . /venv/main/bin/activate && \
    # Record pre-install PyTorch version
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Install application dependencies
    cd /opt/workspace-internal/ && \
    git clone https://github.com/org/app && \
    cd app && \
    uv pip install --no-cache-dir -r requirements.txt && \
    # Verify PyTorch version unchanged
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || \
        { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
```

### Conventions

- Always `set -euo pipefail` in RUN commands
- Activate venv with `. /venv/main/bin/activate`
- Clone into `/opt/workspace-internal/` (auto-synced to `$WORKSPACE` at boot)
- Use `uv pip install` — never plain `pip`
- Verify PyTorch version before and after dependency installs
- End with `env-hash > /.env_hash` to detect environment drift
- Include the three `LABEL` lines
- `COPY ./ROOT /` to install supervisor scripts and configs

### Directory Layout

```
derivatives/pytorch/derivatives/my-app/
├── Dockerfile
└── ROOT/
    ├── etc/
    │   └── supervisor/
    │       └── conf.d/
    │           └── my-app.conf
    └── opt/
        └── supervisor-scripts/
            └── my-app.sh
```

---

## Creating an External Image

Use this only for large, established upstream projects where rebuilding from scratch is impractical (e.g., vLLM, SGLang, Ollama).

### Dockerfile Pattern

Reference: `external/vllm/Dockerfile`

```dockerfile
ARG VLLM_BASE=vllm/vllm-openai:v0.13.0
ARG VAST_BASE=vastai/base-image:stock-ubuntu24.04-py312

FROM ${VAST_BASE} AS vast_base_image
FROM ${VLLM_BASE} AS vllm_build

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="vLLM image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

### Convert non-Vast image to more closely resemble images derived from vastai/base-image ###
SHELL ["/bin/bash", "-c"]
ENV DATA_DIRECTORY=/workspace
ENV WORKSPACE=/workspace
ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/instance-tools/bin:${PATH}
WORKDIR /

COPY --from=base_image_source /ROOT /
COPY --from=base_image_source /portal-aio /opt/portal-aio
COPY --from=vast_base_image /opt/portal-aio/caddy_manager/caddy /opt/portal-aio/caddy_manager/caddy
COPY --from=base_image_source tools/convert-non-vast-image.sh /tmp/convert-non-vast-image.sh

ARG TARGETARCH
RUN \
    set -euo pipefail && \
    chmod +x /tmp/convert-non-vast-image.sh && \
    /tmp/convert-non-vast-image.sh && \
    rm /tmp/convert-non-vast-image.sh

### Begin app-specific configuration ###

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
```

### Key Steps

1. Multi-stage build: pull both the upstream image and `vastai/base-image`
2. Run `convert-non-vast-image.sh` to graft Vast infrastructure onto the upstream image
3. Copy portal-aio and caddy from the Vast base
4. `COPY ./ROOT /` for app-specific supervisor scripts
5. Set `ENTRYPOINT` to Vast's entrypoint

### Directory Layout

```
external/my-app/
├── Dockerfile
└── ROOT/
    ├── etc/
    │   ├── supervisor/
    │   │   └── conf.d/
    │   │       └── my-app.conf
    │   └── vast_boot.d/
    │       └── 05-my-app-env.sh    # Set PORTAL_CONFIG and other env vars
    └── opt/
        └── supervisor-scripts/
            └── my-app.sh
```

---

## Creating a Provisioning-Only Template

Use for first drafts and rapid prototyping. These run at boot time on a stock `vastai/pytorch` (or `vastai/base-image`) instance via the `PROVISIONING_SCRIPT` environment variable.

**Warning:** provisioning-only templates can break over time (upstream changes, network issues at boot, dependency conflicts). Convert to a derivative image once validated.

### Complete Pattern

Reference: `derivatives/pytorch/provisioning_scripts/qwen3-tts.sh`

```bash
#!/bin/bash
set -euo pipefail

# 1. System dependencies (if needed)
apt-get install --no-install-recommends -y sox

# 2. Activate the shared venv
. /venv/main/bin/activate

# 3. Clone the application
cd "${WORKSPACE}"
[[ ! -d My-App ]] && git clone https://github.com/org/My-App

cd My-App

# 4. Install Python dependencies
uv pip install -r requirements.txt

# 5. Create the supervisor startup script
cat > /opt/supervisor-scripts/my-app.sh << 'EOL'
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "My App"

. /venv/main/bin/activate

echo "Starting My App"

cd "${WORKSPACE}/My-App"
python app.py
EOL

chmod +x /opt/supervisor-scripts/my-app.sh

# 6. Create the supervisor config
cat > /etc/supervisor/conf.d/my-app.conf << 'EOL'
[program:my-app]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/my-app.sh
autostart=true
autorestart=true
exitcodes=0
startsecs=0
stopasgroup=true
killasgroup=true
stopsignal=TERM
stopwaitsecs=10
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_events_enabled=true
stdout_logfile_maxbytes=0
stdout_logfile_backups=0
EOL

# 7. Register with supervisor
supervisorctl reread
supervisorctl update
```

### Usage

Set the `PROVISIONING_SCRIPT` environment variable on the Vast.ai template to the raw URL of the script (e.g., a GitHub raw link). The boot sequence downloads and executes it at step 75.

---

## Supervisor Integration

Every application runs as a supervised process. You need two files: a startup script and a config.

### Supervisor Script Template

```bash
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "App Name"

. /venv/main/bin/activate

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed"
    sleep 5
done

echo "Starting App Name"

cd "${WORKSPACE}/my-app"
python app.py
```

### Utilities

Each sourced utility does one thing:

| Utility | What it does |
|---------|-------------|
| `logging.sh` | Redirects stdout/stderr to `/var/log/portal/${PROC_NAME}.log` (tee'd) |
| `cleanup_generic.sh` | Sets a trap to kill all subprocesses on EXIT/INT/TERM |
| `environment.sh` | Sources `/etc/environment` and `${WORKSPACE}/.env` |
| `exit_portal.sh` `"<name>"` | Waits for `/etc/portal.yaml`, exits if the app isn't listed (user toggled it off) |
| `exit_serverless.sh` | Exits if `$SERVERLESS` is `"true"` (skip non-essential services in serverless mode) |

**Source order matters.** Always source in this order: `logging.sh`, `cleanup_generic.sh`, `environment.sh`, then `exit_portal.sh` or `exit_serverless.sh`.

### Supervisor Config Template

```ini
[program:my-app]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/my-app.sh
autostart=true
autorestart=true
exitcodes=0
startsecs=0
stopasgroup=true
killasgroup=true
stopsignal=TERM
stopwaitsecs=10
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_events_enabled=true
stdout_logfile_maxbytes=0
stdout_logfile_backups=0
```

The `environment=PROC_NAME="%(program_name)s"` line is required — it sets the `PROC_NAME` variable used by the utilities.

---

## Portal Integration

The Instance Portal provides tabbed browser access to services. It is configured via the `PORTAL_CONFIG` environment variable.

### Format

```
PORTAL_CONFIG="localhost:internal:external:path:Label|localhost:internal:external:path:Label|..."
```

Fields are colon-separated, entries are pipe-separated:

| Field | Example | Description |
|-------|---------|-------------|
| Host | `localhost` | Always `localhost` |
| Internal port | `7860` | Port the app listens on inside the container |
| External port | `17860` | Port exposed to the user (convention: internal + 10000) |
| Path | `/` | URL path for the tab link |
| Label | `My App` | Tab label shown in the portal |

### Example

```bash
PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:7860:17860:/:My App|localhost:8080:18080:/:Jupyter"
```

### How Supervisor Scripts Check Portal Config

The `exit_portal.sh` utility checks whether the app's label appears in `/etc/portal.yaml`. If a user removes the entry, the supervisor script exits gracefully — this is how users toggle apps on and off.

```bash
# From exit_portal.sh — searches for the app name in portal config
if ! grep -qiE "^[^#].*${search_term}" /etc/portal.yaml; then
    echo "Skipping ${PROC_NAME} startup (not in /etc/portal.yaml)"
    sleep 6
    exit 0
fi
```

---

## Python Environment

- **Single shared venv** at `/venv/main/` — all applications share it
- **Always use `uv pip install`** — never plain `pip`
- **PyTorch installs must target a concrete backend:**
  ```bash
  uv pip install torch torchvision torchaudio --torch-backend cu128
  ```
  Never use `--torch-backend=auto` — it can select the wrong backend and produce non-reproducible builds.
- In derivative Dockerfiles, verify PyTorch version before and after installing dependencies to catch accidental overwrites

---

## CI/CD (GitHub Actions)

Full CI/CD conventions are in [`.github/AGENTS.md`](.github/AGENTS.md). Key points:

- Every image has a workflow at `.github/workflows/build-<name>.yml`
- **4-job pipeline:** `preflight` → `build` → `collect-tags` → `notify`
- The `build` job always uses a matrix strategy (even for single-variant images)
- Tags with commit hashes include an ISO 8601 date (`v1-a1b2c3d-2026-02-02-cuda-12.9`); version tags do not

### Checklist for New Workflows

- [ ] File named `.github/workflows/build-<name>.yml`
- [ ] Schedule cron: `'0 0,12 * * *'`
- [ ] `workflow_dispatch` inputs: VERSION/REF, DOCKERHUB_REPO, MULTI_ARCH, CUSTOM_IMAGE_TAG
- [ ] `env` block: DEFAULT_DOCKERHUB_REPO, DEFAULT_MULTI_ARCH, RELEASE_AGE_THRESHOLD
- [ ] 4 jobs: `preflight` → `build` → `collect-tags` → `notify`
- [ ] `build` uses `strategy.matrix` and derives `MATRIX_ID` via `md5sum | cut -c1-8`
- [ ] `collect-tags` and `notify` jobs copied verbatim from an existing workflow

See [`.github/AGENTS.md`](.github/AGENTS.md) for the full specification including code templates.

---

## Quick-Start Checklists

### New Derivative Image (Preferred)

1. Create `derivatives/pytorch/derivatives/<name>/Dockerfile`
2. Create `derivatives/pytorch/derivatives/<name>/ROOT/opt/supervisor-scripts/<name>.sh`
3. Create `derivatives/pytorch/derivatives/<name>/ROOT/etc/supervisor/conf.d/<name>.conf`
4. Follow the [Dockerfile pattern](#dockerfile-pattern) — `FROM`, labels, `COPY ./ROOT /`, venv activate, install, PyTorch verify, `env-hash`
5. Follow the [supervisor script template](#supervisor-script-template) and [config template](#supervisor-config-template)
6. Add a CI workflow at `.github/workflows/build-<name>.yml` (see [CI/CD](#cicd-github-actions))
7. Test locally with `docker build`

### New External Image (Large Trusted Upstream Only)

1. Create `external/<name>/Dockerfile`
2. Create `external/<name>/ROOT/opt/supervisor-scripts/<name>.sh`
3. Create `external/<name>/ROOT/etc/supervisor/conf.d/<name>.conf`
4. Create `external/<name>/ROOT/etc/vast_boot.d/05-<name>-env.sh` to set `PORTAL_CONFIG`
5. Follow the [external Dockerfile pattern](#dockerfile-pattern-1) — multi-stage build, `convert-non-vast-image.sh`, `COPY ./ROOT /`
6. Add a CI workflow at `.github/workflows/build-<name>.yml`

### New Provisioning-Only Template (Prototyping)

1. Create `derivatives/pytorch/provisioning_scripts/<name>.sh` (or `provisioning_scripts/<name>.sh` for base-image)
2. Follow the [complete provisioning pattern](#complete-pattern) — deps, venv, clone, install, supervisor script, supervisor config, `supervisorctl reread && update`
3. Test by setting `PROVISIONING_SCRIPT` to the raw URL on a Vast.ai instance
4. Plan to convert to a derivative image once the template is validated
