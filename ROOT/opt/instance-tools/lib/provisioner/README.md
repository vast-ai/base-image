# Declarative Provisioner

A Python module that replaces ad-hoc bash provisioning scripts with a declarative YAML manifest. Instead of writing hundreds of lines of bash boilerplate for apt/pip installation, model downloads, retry logic, and supervisor service registration, you declare what you need in YAML and the provisioner handles it.

## Quick Start

### 1. Write a manifest

```yaml
version: 1

settings:
  workspace: "${WORKSPACE:-/workspace}"
  venv: "/venv/main"

apt_packages:
  - libasound2-dev

pip_packages:
  packages:
    - torch
    - torchaudio

downloads:
  - url: "https://huggingface.co/org/repo/resolve/main/model.safetensors"
    dest: "${WORKSPACE}/models/model.safetensors"

services:
  - name: "my-app"
    portal_search_term: "My App"
    workdir: "${WORKSPACE}/my-app"
    command: "python app.py --port 7860"
```

### 2. Run it

```bash
# Direct invocation
provisioner manifest.yaml

# Dry run (prints what would be done without executing)
provisioner manifest.yaml --dry-run

# Via environment variable (set in Vast.ai template)
PROVISIONING_MANIFEST=https://example.com/manifest.yaml
```

## How It Works

The provisioner executes 8 phases sequentially:

| Phase | Action | Parallelism | On failure |
|-------|--------|-------------|------------|
| 1 | Load & validate manifest, expand env vars | - | Abort |
| 2 | Validate auth tokens, resolve conditional downloads | - | Abort |
| 3 | Install apt packages | Sequential | **Fail-fast** |
| 4 | Clone git repos | Parallel (ThreadPool) | **Fail-fast** |
| 5 | Install pip packages + requirements files | Sequential (after git) | **Fail-fast** |
| 6 | Download files | Parallel (two pools: HF + wget) | **Best-effort** |
| 7 | Register supervisor services | Sequential | **Always runs** |
| 8 | Run post_commands | Sequential | **Always runs** |

### Error Handling

Not all failures are equal. A missing system package means the app can't start, but a flaky CivitAI download shouldn't prevent the app from registering with supervisor.

- **Phases 3-5 (apt, git, pip)** are **fail-fast**. These are hard dependencies -- if any fails, the provisioner skips all remaining phases and exits immediately. The app can't function without its code and packages.
- **Phase 6 (downloads)** is **best-effort**. Individual download failures are logged, but execution continues to phases 7-8. Each download already retries with exponential backoff before giving up, so a failure here means persistent issues (rate limits, bad URLs, revoked tokens). The app may still start with partial models.
- **Phases 7-8 (services, post_commands)** **always run**, even if some downloads failed. The app must be registered with supervisor so it can start and potentially recover. Failures in these phases are logged but don't prevent each other from running.

The exit code is **0 only when everything succeeds**. Any failure in any phase results in exit code 1, which tells the boot script not to set the `/.provisioning_manifest_complete` sentinel -- so provisioning will be retried on the next restart.

## Boot Integration

When the `PROVISIONING_MANIFEST` environment variable is set, the boot script (`75-provisioning-script.sh`) processes it automatically on instance startup:

- If the value is a URL, it is downloaded first
- If the value is a local file path, it is used directly
- `PROVISIONING_SCRIPT` (bash) runs first if present -- the two are independent
- A sentinel file `/.provisioning_manifest_complete` prevents re-running on restart

## Manifest Reference

### `version`

Required. Must be `1`.

```yaml
version: 1
```

### `settings`

Global configuration. All fields have defaults.

```yaml
settings:
  workspace: "${WORKSPACE:-/workspace}"       # Base path for relative references
  venv: "/venv/main"                          # Python virtual environment
  log_file: "${PROVISIONING_LOG:-/var/log/portal/provisioning.log}"
  concurrency:
    hf_downloads: 3                           # Max parallel HuggingFace downloads
    wget_downloads: 5                         # Max parallel wget downloads
  retry:
    max_attempts: 5                           # Retries per download
    initial_delay: 2                          # Seconds before first retry
    backoff_multiplier: 2                     # Exponential backoff factor (2, 4, 8, 16...)
```

### `auth`

Names the environment variables that hold API tokens. Tokens are validated at startup and used to resolve conditional downloads.

```yaml
auth:
  huggingface:
    token_env: "HF_TOKEN"                     # Validated via HF whoami API
  civitai:
    token_env: "CIVITAI_TOKEN"                # Validated via CivitAI API
```

### `apt_packages`

System packages to install via `apt-get`.

```yaml
apt_packages:
  - libasound2-dev
  - sox
  - ffmpeg
```

### `pip_packages`

Python packages. Installed after git clones so that requirements files from cloned repos are available.

```yaml
pip_packages:
  tool: "uv"                                  # "uv" (default) or "pip"
  packages:
    - "torch==${TORCH_VERSION:-2.8.0}"
    - torchaudio
  args: "--torch-backend ${TORCH_BACKEND:-cu128}"  # Extra args passed to installer
  requirements:
    - "${WORKSPACE}/app/requirements.txt"
```

### `git_repos`

Git repositories to clone. Cloned in parallel.

```yaml
git_repos:
  - url: "https://github.com/org/repo"
    dest: "${WORKSPACE}/repo"
    ref: "${APP_REF:-}"                       # Empty = default branch
    recursive: true                           # --recursive flag (default: true)
    pull_if_exists: false                     # git pull if dest exists (default: false)
    requirements: "requirements.txt"          # Relative to cloned dir, auto-installed
    pip_install_editable: false               # pip install -e (default: false)
```

### `downloads`

Files to download. URL type is auto-detected:

| URL Pattern | Handler | Auth |
|-------------|---------|------|
| `huggingface.co` | `huggingface-cli download` | `$HF_TOKEN` (automatic) |
| `civitai.com` | `wget` + Authorization header | `$CIVITAI_TOKEN` |
| Anything else | Plain `wget` | None |

```yaml
downloads:
  - url: "https://huggingface.co/org/repo/resolve/main/model.safetensors"
    dest: "${WORKSPACE}/models/model.safetensors"
  - url: "https://civitai.com/api/download/models/12345"
    dest: "${WORKSPACE}/models/Lora/"         # Trailing / = content-disposition filename
```

Features:
- **Parallel downloads** with configurable pool sizes (HF and wget pools are independent)
- **File locking** (`fcntl.flock`) prevents concurrent downloads of the same file
- **Retry with exponential backoff** on failure
- **Skip existing files** (checked inside the lock to prevent race conditions)
- **Trailing slash** on `dest` resolves the filename from the server's Content-Disposition header

### `conditional_downloads`

Downloads that depend on token validity. Useful for gated models with open fallbacks.

```yaml
conditional_downloads:
  - when: "hf_token_valid"
    downloads:
      - url: "https://huggingface.co/org/gated-model/resolve/main/model.safetensors"
        dest: "${WORKSPACE}/models/model.safetensors"
    else_downloads:
      - url: "https://huggingface.co/org/open-model/resolve/main/model.safetensors"
        dest: "${WORKSPACE}/models/model.safetensors"
```

Supported conditions:
- `hf_token_valid` -- true if `$HF_TOKEN` passes the HuggingFace whoami API check

### `env_merge`

Merge additional downloads from environment variables at runtime. This is for user-supplied model lists using the existing `"url|path"` semicolon-separated format from bash provisioning scripts.

```yaml
env_merge:
  HF_MODELS: downloads                       # Parse $HF_MODELS, append to downloads
  CIVITAI_MODELS: downloads
  WGET_DOWNLOADS: downloads
```

Environment variable format:
```
HF_MODELS="https://hf.co/org/repo/resolve/main/a.safetensors|/workspace/models/a.safetensors;https://hf.co/org/repo/resolve/main/b.safetensors|/workspace/models/b.safetensors"
```

### `services`

Register supervisor services. Generates a startup script and `.conf` file matching the conventions used by existing services in this image.

```yaml
services:
  - name: "my-app"                            # Used for filenames and supervisor program name
    portal_search_term: "My App"              # Checked against /etc/portal.yaml
    skip_on_serverless: true                  # Skip startup if SERVERLESS=true (default: true)
    venv: "/venv/main"                        # Virtual environment to activate
    workdir: "${WORKSPACE}/my-app"            # Working directory for the command
    command: "python app.py --port 7860"      # The command to run
    wait_for_provisioning: true               # Wait for /.provisioning to be removed (default: true)
    environment:                              # Environment variables exported in the script
      GRADIO_SERVER_NAME: "127.0.0.1"
```

Generated files:
- `/opt/supervisor-scripts/{name}.sh` -- startup script (sources utils, activates venv, exports env, runs command)
- `/etc/supervisor/conf.d/{name}.conf` -- supervisor config (autostart, autorestart, stdout logging)

After writing the files, runs `supervisorctl reread && supervisorctl update`.

### `post_commands`

Escape hatch for anything not covered by other sections. Commands run sequentially via `shell=True`.

```yaml
post_commands:
  - "ln -sf /workspace/models /workspace/app/models"
  - "chmod +x /workspace/app/run.sh"
```

## Environment Variable Expansion

All string values in the manifest support bash-style variable expansion:

| Syntax | Behavior |
|--------|----------|
| `${VAR}` | Value of `$VAR`, empty string if unset |
| `${VAR:-default}` | Value of `$VAR`, or `default` if unset |
| `${VAR:=default}` | Same as `:-` (value of `$VAR`, or `default` if unset) |

Expansion is applied recursively to all string values before validation.

## Module Structure

```
/opt/instance-tools/lib/provisioner/
    __init__.py              # Package init, version
    __main__.py              # CLI: argparse, 8-phase orchestration
    schema.py                # Dataclasses for manifest structure + validation
    manifest.py              # YAML loading, env var expansion, env_merge, conditionals
    auth.py                  # Token validation (HF, CivitAI) via urllib
    concurrency.py           # ThreadPoolExecutor pools + fcntl.flock file locking
    log.py                   # Logging setup (stdout + file, timestamped)
    downloaders/
        __init__.py
        base.py              # retry_with_backoff() helper
        huggingface.py       # Parse HF URL -> repo/file, run huggingface-cli, move to dest
        wget.py              # wget subprocess with auth header support
    installers/
        __init__.py
        apt.py               # apt-get update + install
        pip.py               # uv pip install / pip install (packages + requirements)
        git.py               # git clone + checkout + optional requirements install
    supervisor.py            # Generate startup script + .conf from templates
    tests/                   # Test suite (see below)
```

## CLI Usage

```
usage: provisioner [-h] [--dry-run] manifest

Declarative instance provisioner

positional arguments:
  manifest    Path to YAML manifest file

options:
  -h, --help  show this help message and exit
  --dry-run   Print what would be done without executing
```

The `provisioner` wrapper at `/opt/instance-tools/bin/provisioner` activates the main venv and sets `PYTHONPATH` automatically.

## Running Tests

Install pytest (not included in the base image):

```bash
pip install pytest
```

Run from the repository root:

```bash
# Run all tests
python -m pytest ROOT/opt/instance-tools/lib/provisioner/tests/ -v

# Run a specific test file
python -m pytest ROOT/opt/instance-tools/lib/provisioner/tests/test_schema.py -v

# Run tests matching a pattern
python -m pytest ROOT/opt/instance-tools/lib/provisioner/tests/ -v -k "test_env"
```

Tests use `unittest.mock` to avoid real subprocess calls, network requests, and filesystem side effects. No external services or tokens are needed to run the full suite.

## Full Example Manifest

```yaml
version: 1

settings:
  workspace: "${WORKSPACE:-/workspace}"
  venv: "/venv/main"
  log_file: "${PROVISIONING_LOG:-/var/log/portal/provisioning.log}"
  concurrency:
    hf_downloads: 3
    wget_downloads: 5
  retry:
    max_attempts: 5
    initial_delay: 2
    backoff_multiplier: 2

auth:
  huggingface:
    token_env: "HF_TOKEN"
  civitai:
    token_env: "CIVITAI_TOKEN"

apt_packages:
  - libasound2-dev
  - sox

pip_packages:
  tool: "uv"
  packages:
    - "torch==${TORCH_VERSION:-2.8.0}"
    - torchaudio
  args: "--torch-backend ${TORCH_BACKEND:-cu128}"
  requirements:
    - "${WORKSPACE}/app/requirements.txt"

git_repos:
  - url: "https://github.com/org/repo"
    dest: "${WORKSPACE}/repo"
    ref: "${APP_REF:-}"
    recursive: true
    pull_if_exists: false
    requirements: "requirements.txt"
    pip_install_editable: false

downloads:
  - url: "https://huggingface.co/org/repo/resolve/main/model.safetensors"
    dest: "${WORKSPACE}/models/model.safetensors"
  - url: "https://civitai.com/api/download/models/12345"
    dest: "${WORKSPACE}/models/Stable-diffusion/"

conditional_downloads:
  - when: "hf_token_valid"
    downloads:
      - url: "https://huggingface.co/org/gated-model/resolve/main/model.safetensors"
        dest: "${WORKSPACE}/models/gated.safetensors"
    else_downloads:
      - url: "https://huggingface.co/org/open-model/resolve/main/model.safetensors"
        dest: "${WORKSPACE}/models/open.safetensors"

env_merge:
  HF_MODELS: downloads
  CIVITAI_MODELS: downloads
  WGET_DOWNLOADS: downloads

services:
  - name: "my-app"
    portal_search_term: "My App"
    skip_on_serverless: true
    venv: "/venv/main"
    workdir: "${WORKSPACE}/my-app"
    command: "python app.py --port 7860"
    wait_for_provisioning: true
    environment:
      GRADIO_SERVER_NAME: "127.0.0.1"

post_commands:
  - "echo 'Provisioning complete'"
```
