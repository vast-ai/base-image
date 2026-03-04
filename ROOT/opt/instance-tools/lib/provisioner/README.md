# Provisioner

A declarative, YAML-driven instance provisioner for Vast.ai base images. Instead of writing imperative shell scripts, you describe *what* should be installed in a manifest file and the provisioner handles execution order, parallelism, idempotency, and failure handling.

```
provisioner manifest.yaml [--dry-run] [--force]
```

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Execution Phases](#execution-phases)
- [Manifest Reference](#manifest-reference)
  - [settings](#settings)
  - [auth](#auth)
  - [on_failure](#on_failure)
  - [write_files / write_files_late](#write_files--write_files_late)
  - [apt_packages](#apt_packages)
  - [pip_packages](#pip_packages)
  - [conda_packages](#conda_packages)
  - [git_repos](#git_repos)
  - [downloads](#downloads)
  - [conditional_downloads](#conditional_downloads)
  - [env_merge](#env_merge)
  - [services](#services)
  - [post_commands](#post_commands)
- [Environment Variable Expansion](#environment-variable-expansion)
- [Idempotency](#idempotency)
- [Failure Handling](#failure-handling)
- [CLI Reference](#cli-reference)
- [Running Tests](#running-tests)
- [Examples](#examples)

## Quick Start

```yaml
version: 1

pip_packages:
  - packages: [torch, torchvision]
    args: "--torch-backend auto"

git_repos:
  - url: https://github.com/your-org/your-app
    dest: /workspace/your-app
    post_commands:
      - "uv pip install --no-cache --python /venv/main/bin/python -r requirements.txt"

services:
  - name: your-app
    portal_search_term: "Your App"
    workdir: /workspace/your-app
    command: "python app.py --port 7860"
```

```bash
# Dry run (prints what would happen)
provisioner manifest.yaml --dry-run

# Execute
provisioner manifest.yaml

# Force re-run all phases (ignore cached state)
provisioner manifest.yaml --force
```

## Architecture

```
bin/provisioner                     Shell wrapper (activates provisioner venv, exec python -m provisioner)
lib/provisioner/
├── __main__.py                     Pipeline orchestrator and CLI entry point
├── schema.py                       Manifest dataclasses and validation
├── manifest.py                     YAML loading, env expansion, conditionals
├── state.py                        Content-hash idempotency (/.provisioner_state/)
├── failure.py                      Failure actions (continue/retry/destroy/stop)
├── auth.py                         HuggingFace and CivitAI token validation
├── log.py                          Logging setup (stdout + file)
├── concurrency.py                  Thread pool runner + file locking
├── supervisor.py                   Supervisor startup script + .conf generation
├── installers/
│   ├── apt.py                      apt-get install
│   ├── pip.py                      uv/pip install with multi-venv support
│   ├── conda.py                    mamba/conda install via Miniforge3
│   ├── git.py                      Parallel git clone with post-commands
│   └── files.py                    Cloud-init style file writer
├── downloaders/
│   ├── base.py                     Retry with exponential backoff
│   ├── huggingface.py              huggingface-cli download
│   └── wget.py                     wget download (with CivitAI auth)
├── examples/                       Example manifests
└── tests/                          Test suite (pytest)
```

The provisioner runs in its own isolated venv at `/opt/instance-tools/provisioner/venv/`, completely separate from the application venv at `/venv/main`. The `bin/provisioner` wrapper handles venv activation and `PYTHONPATH` setup.

## Execution Phases

Phases run sequentially in this order:

| Phase | Description | Failure Mode |
|-------|-------------|--------------|
| 1 | Load manifest, expand env vars | Abort |
| 2 | Validate auth tokens, resolve conditionals, apply env_merge | Non-fatal |
| 2b | Write early files (`write_files`) | **Fail-fast** |
| 3 | Install apt packages | **Fail-fast** |
| 4 | Clone git repos + run post_commands (parallel) | **Fail-fast** |
| 5 | Install pip packages (per block, sequential) | **Fail-fast** |
| 5b | Install conda packages | **Fail-fast** |
| 6 | Download files (parallel, two pools: HF + wget) | **Best-effort** |
| 7 | Register supervisor services | Always runs |
| 7b | Write late files (`write_files_late`) | Always runs |
| 8 | Run post_commands | Always runs |

**Fail-fast** means a failure in that phase skips all remaining phases and exits immediately with code 1. This is appropriate for dependencies -- the app cannot run without its packages.

**Best-effort** means individual failures are logged but execution continues. Missing model files are recoverable; the app may still start.

**Always runs** means phases 7-8 execute regardless of download failures. Services must be registered with supervisor even if some models are still missing.

The exit code is 0 on full success, 1 if anything failed. A non-zero exit tells the boot script not to mark provisioning as complete, so it will be retried on next restart.

## Manifest Reference

Every manifest starts with `version: 1`. All other sections are optional.

### settings

```yaml
settings:
  workspace: "${WORKSPACE:-/workspace}"
  venv: "/venv/main"
  log_file: "/var/log/portal/provisioning.log"
  concurrency:
    hf_downloads: 3
    wget_downloads: 5
  retry:
    max_attempts: 5
    initial_delay: 2          # seconds before first retry
    backoff_multiplier: 2     # exponential backoff (delays: 2s, 4s, 8s, 16s)
```

All values shown are defaults. `venv` is the default target for pip installs when no block-level venv is specified. `retry` controls download retry behavior.

### auth

```yaml
auth:
  huggingface:
    token_env: "HF_TOKEN"
  civitai:
    token_env: "CIVITAI_TOKEN"
```

Names the environment variables that hold API tokens. Tokens are **never** stored in the manifest. The provisioner validates tokens by making live API calls during phase 2 -- the results gate [conditional_downloads](#conditional_downloads).

A network failure or missing token returns "invalid", which selects the `else_downloads` branch.

### on_failure

```yaml
on_failure:
  action: continue        # continue | retry | destroy | stop
  max_retries: 3          # only used by retry
  webhook: ""             # URL to POST failure info (optional)
```

Controls what happens when provisioning fails:

| Action | Behavior |
|--------|----------|
| `continue` | Log the failure, exit 1. Default. |
| `retry` | Increment attempt counter (`/.provisioner_attempts`). If under `max_retries`, exit 1 and let the boot script re-run. If limit exceeded, fall through to `continue`. |
| `destroy` | Call `vastai destroy instance $CONTAINER_ID --api-key $CONTAINER_API_KEY`. |
| `stop` | Call `vastai stop instance $CONTAINER_ID --api-key $CONTAINER_API_KEY`. |

**Webhook:** If configured, a JSON payload is POSTed on failure:

```json
{
  "action": "retry",
  "manifest": "/path/to/manifest.yaml",
  "error": "Pip installation failed: ...",
  "container_id": "12345",
  "timestamp": "2025-01-15T10:30:00"
}
```

The `PROVISIONER_WEBHOOK_URL` environment variable overrides `on_failure.webhook` in the manifest, allowing operators to set a global webhook without modifying manifests.

### write_files / write_files_late

Cloud-init style file writing in two phases: `write_files` runs early (phase 2b, fail-fast) and `write_files_late` runs late (phase 7b, always runs).

```yaml
# Phase 2b: Written immediately after auth validation, before apt/pip/git
# Use for config files that must exist before packages are installed
write_files:
  - path: /etc/app/config.yaml
    content: |
      database: postgres://localhost/app
      debug: false
    permissions: "0644"           # Octal (default: "0644")
    owner: "appuser:appgroup"    # Optional user:group or user

  - path: /opt/scripts/setup.sh
    content: |
      #!/bin/bash
      echo "Setup complete"
    permissions: "0755"

# Phase 7b: Written after services are registered, before post_commands
# Use for config that depends on cloned repos or installed packages
write_files_late:
  - path: /workspace/app/.env
    content: |
      MODEL_PATH=/workspace/models
      PORT=7860
    permissions: "0600"
```

| Field | Default | Description |
|-------|---------|-------------|
| `path` | | Absolute path to write (parent dirs created automatically) |
| `content` | `""` | File content (supports env var expansion like all manifest strings) |
| `permissions` | `"0644"` | Octal permission string |
| `owner` | `""` | Optional `user:group` or `user` (falls back to user's primary group) |

**Early vs late:** Use `write_files` for config files needed during installation (apt sources, pip config, git config). Use `write_files_late` for app config that references paths created during provisioning or that should be regenerated on each run.

**Owner handling:** If the specified user/group doesn't exist or the process lacks permission to chown, a warning is logged but the file is still written with its content and permissions.

### apt_packages

```yaml
apt_packages:
  - ffmpeg
  - libsndfile1
  - "ffmpeg=7:6.1.1-3ubuntu5"   # version pinning with =
```

Installed via `apt-get update -qq && apt-get install -y -qq --no-install-recommends`. Failure is fatal (fail-fast). Always runs `apt-get update` first to ensure fresh package lists.

### pip_packages

A list of install blocks. Each block can target a different venv with different options.

```yaml
pip_packages:
  # Standard install into default venv (/venv/main)
  - packages: [torch, torchaudio]
    args: "--torch-backend auto"

  # Different venv, auto-created with specific Python version
  - venv: "/venv/custom"
    python: "3.11"
    tool: pip                     # "uv" (default) or "pip"
    packages:
      - "numpy==1.24.0"
      - "pandas<2.0"
    requirements:
      - /workspace/app/requirements.txt

  # System python (no venv)
  - venv: system
    packages: [setuptools, wheel]
```

| Field | Default | Description |
|-------|---------|-------------|
| `venv` | `""` (uses `settings.venv`) | Target venv path, or `"system"` for system python |
| `python` | `""` | Python version for auto-creating the venv (e.g. `"3.11"`) |
| `tool` | `"uv"` | Install tool: `"uv"` or `"pip"` |
| `packages` | `[]` | Package specifiers (version operators: `>=`, `==`, `~=`, `!=`, `<`, `>`) |
| `args` | `""` | Extra arguments appended to the install command |
| `requirements` | `[]` | Paths to requirements files |

**Automatic flags** (always added, do not specify manually):

| Flag | When |
|------|------|
| `--no-cache` (uv) / `--no-cache-dir` (pip) | Always |
| `--break-system-packages` | When `venv: system` |
| `--system` | When `venv: system` and `tool: uv` |

**Auto-venv creation:** When `venv` or `python` is explicitly set, the provisioner creates the venv if it doesn't exist (using `uv venv` or `python -m venv`). The default `/venv/main` is assumed to pre-exist in the base image.

**Backward compatibility:** A single dict (old format) is automatically wrapped in a list:
```yaml
# This still works:
pip_packages:
  packages: [torch]
```

### conda_packages

```yaml
conda_packages:
  packages:
    - "cudatoolkit=11.8"
    - "nccl>=2.18"
  channels:
    - conda-forge
    - nvidia
  env: "/venv/conda-ml"          # target conda prefix env (auto-created)
  python: "3.11"                  # for env auto-creation
  args: "--no-update-deps"
```

Uses the Miniforge3 installation at `/opt/miniforge3/`. Prefers `mamba` (faster dependency solver), falls back to `conda`. The tool is located by checking `/opt/miniforge3/bin/` first, then `PATH`.

| Field | Default | Description |
|-------|---------|-------------|
| `packages` | `[]` | Package specifiers (conda syntax: `=` exact, `>=`, `<=`, `<`, `>`) |
| `channels` | `[]` | Extra channels passed as `-c` flags |
| `env` | `""` | Target conda prefix environment path |
| `python` | `""` | Python version for env auto-creation |
| `args` | `""` | Extra arguments |

Without `env`, packages install into the base/active conda environment. With `env`, the prefix is auto-created if the `conda-meta/` directory doesn't exist, using `{tool} create -y -p {path} [python={version}]`.

### git_repos

```yaml
git_repos:
  - url: https://github.com/org/repo
    dest: "${WORKSPACE:-/workspace}/repo"
    ref: "v2.0"
    recursive: true
    pull_if_exists: false
    post_commands:
      - "uv pip install --no-cache --python /venv/main/bin/python -r requirements.txt"
      - "uv pip install --no-cache --python /venv/main/bin/python -e ."
      - "sed -i 's/old/new/' config.yaml"
```

All repos are cloned **in parallel** (4 workers). Each repo's `post_commands` run in its directory immediately after clone+checkout, within the parallel pool.

| Field | Default | Description |
|-------|---------|-------------|
| `url` | | Repository URL |
| `dest` | | Local destination path |
| `ref` | `""` | Tag, branch, or commit to checkout after cloning |
| `recursive` | `true` | Pass `--recursive` to git clone (handles submodules) |
| `pull_if_exists` | `false` | If dest exists: `true` runs `git pull`, `false` skips |
| `post_commands` | `[]` | Shell commands run in repo dir after clone+checkout |

**post_commands** run with `shell=True` and `cwd` set to the repo's `dest` directory. Use them for:
- Installing requirements: `uv pip install --no-cache --python /venv/main/bin/python -r requirements.txt`
- Editable installs: `uv pip install --no-cache --python /venv/main/bin/python -e .`
- Patching files, setting permissions, running setup scripts

Since post_commands run within the parallel clone pool, repos with heavier post-processing don't block other repos from cloning.

### downloads

```yaml
downloads:
  - url: https://huggingface.co/org/model/resolve/main/weights.safetensors
    dest: /workspace/models/weights.safetensors
  - url: https://civitai.com/api/download/models/12345
    dest: /workspace/models/loras/        # trailing / = resolve filename from server
  - url: https://example.com/file.bin
    dest: /workspace/other/file.bin
```

Downloads run in two independent parallel pools:

| URL Pattern | Handler | Auth | Pool |
|-------------|---------|------|------|
| `huggingface.co` | `huggingface-cli download` | `$HF_TOKEN` (automatic) | `hf_downloads` |
| `civitai.com` | `wget` with `Authorization` header | `$CIVITAI_TOKEN` | `wget_downloads` |
| Everything else | `wget` | None | `wget_downloads` |

**Trailing `/` on dest:** The provisioner resolves the filename from the server's `Content-Disposition` header (falling back to the URL's last path segment) and appends it to the directory path.

**Features:**
- Parallel downloads with configurable pool sizes
- `fcntl`-based file locking prevents concurrent downloads of the same file
- Retry with exponential backoff on failure
- Existing files are skipped (checked inside the lock to prevent races)
- HuggingFace downloads use a temp directory and atomic move to dest
- Failed wget downloads clean up partial files

### conditional_downloads

```yaml
conditional_downloads:
  - when: hf_token_valid
    downloads:
      - url: https://huggingface.co/org/gated-model/resolve/main/model.safetensors
        dest: /workspace/models/gated.safetensors
    else_downloads:
      - url: https://huggingface.co/org/open-model/resolve/main/model.safetensors
        dest: /workspace/models/fallback.safetensors
```

Resolved during phase 2 based on token validation results. The selected entries are merged into the main `downloads` list before phase 6 runs.

**Supported conditions:** `hf_token_valid`, `civitai_token_valid`

### env_merge

```yaml
env_merge:
  HF_MODELS: downloads
  CIVITAI_MODELS: downloads
  EXTRA_DOWNLOADS: downloads
```

Parses environment variables as semicolon-separated `url|path` entries and appends them to the `downloads` list at runtime. Only the `"downloads"` target is supported.

```bash
# Example:
export HF_MODELS="https://hf.co/org/model/resolve/main/a.safetensors|/workspace/models/a.safetensors;https://hf.co/org/model/resolve/main/b.safetensors|/workspace/models/b.safetensors"
```

Lines starting with `#` are treated as comments and skipped. Entries without `|` are warned and skipped.

### services

```yaml
services:
  - name: my-app
    portal_search_term: "My App"
    skip_on_serverless: true
    venv: "/venv/main"
    workdir: /workspace/app
    command: "python app.py --port 7860"
    pre_commands:
      - "ln -sf /models ./models"
      - "python setup_config.py"
    wait_for_provisioning: true
    environment:
      GRADIO_SERVER_NAME: "0.0.0.0"
```

Generates two files per service, then reloads supervisor:
- `/opt/supervisor-scripts/{name}.sh` -- startup script
- `/etc/supervisor/conf.d/{name}.conf` -- supervisor config

| Field | Default | Description |
|-------|---------|-------------|
| `name` | | Service name (used for filenames and supervisor program name) |
| `portal_search_term` | `""` | Checked against `/etc/portal.yaml` -- service exits if not found |
| `skip_on_serverless` | `true` | Exit startup script if `$SERVERLESS=true` |
| `venv` | `"/venv/main"` | Venv to activate in the startup script |
| `workdir` | `""` | Working directory (`cd` before running command) |
| `command` | `""` | Launch command -- must be the last line in the script |
| `pre_commands` | `[]` | Shell commands run after cd/env exports, before the launch command |
| `wait_for_provisioning` | `true` | Poll-wait for `/.provisioning` to be removed before starting |
| `environment` | `{}` | Extra env vars exported in the startup script |

**Generated startup script structure:**

```bash
#!/bin/bash
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"           # if skip_on_serverless
. "${utils}/exit_portal.sh" "My App"      # if portal_search_term set

. /venv/main/bin/activate

# Wait for provisioning                  # if wait_for_provisioning
while [ -f "/.provisioning" ]; do
    sleep 5
done

export GRADIO_SERVER_NAME="0.0.0.0"

cd /workspace/app
ln -sf /models ./models                   # pre_commands
python setup_config.py
python app.py --port 7860                 # command (last line)
```

The `command` must be the last line because it becomes the process that supervisor monitors. Use `pre_commands` for any setup needed at service startup time (symlinks, config generation, migrations).

### post_commands

```yaml
post_commands:
  - "ln -sf /workspace/models /workspace/app/models"
  - "npm install --prefix /workspace/web-ui"
  - "chmod +x /workspace/app/run.sh"
```

Arbitrary shell commands run sequentially after all other phases. Each command runs with `shell=True`. A failed command logs an error but does not prevent subsequent commands from running. Use this for npm installs, symlinks, config generation, or anything not covered by other sections.

## Environment Variable Expansion

All string values in the manifest support bash-style variable expansion, applied recursively before any processing:

| Syntax | Behavior |
|--------|----------|
| `${VAR}` | Value of `$VAR`, or empty string if unset |
| `${VAR:-default}` | Value of `$VAR`, or `default` if unset |
| `${VAR:=default}` | Same as `:-` |

```yaml
# Common patterns:
dest: "${WORKSPACE:-/workspace}/ComfyUI"
ref: "${COMFYUI_VERSION:-main}"
command: "python app.py ${APP_ARGS:---port 7860 --host 0.0.0.0}"
```

Expansion happens on the raw YAML dict before dataclass construction, so every field -- settings paths, URLs, commands, token env names -- can reference environment variables.

## Idempotency

Each phase computes a SHA-256 content hash of its inputs before execution. If the stored hash matches the one from the last successful run, the entire phase is skipped.

**Storage:** `/.provisioner_state/{stage_name}.hash`

| Stage | Hash Inputs |
|-------|-------------|
| write_files | Ordered (path, content, permissions, owner) tuples |
| apt | Sorted package list |
| git | Sorted (url, ref, recursive, pull_if_exists, post_commands) tuples |
| pip | Per-block: (venv, tool, packages, args, requirements, python) |
| conda | (packages, channels, args, env, python) |
| downloads | Sorted (url, dest) tuples |
| services | Sorted (name, command, venv, workdir, environment) tuples |
| write_files_late | Ordered (path, content, permissions, owner) tuples |
| post_commands | Ordered command list |

**`--force`** clears all stored hashes, forcing every phase to re-run.

**`--dry-run`** never reads or writes state. All phases execute in dry-run mode regardless of stored hashes.

**Edge cases:**
- The git hash covers repo configuration, not repo contents. Manually editing a cloned repo doesn't invalidate the hash. Set `pull_if_exists: true` if you need to force updates.
- The download stage hash covers the URL list. Individual file-exists checks still run within the phase, so already-downloaded files are skipped even when the phase re-runs.
- Download and post_commands hashes are only stored when no errors occurred in those phases.
- State lives at `/.provisioner_state/` (root filesystem), surviving workspace resets but wiped on container rebuild.

## Failure Handling

Failure behavior depends on the phase:

- **Phase 2b (write_files):** Fail-fast. Config files needed by later phases must be written successfully.
- **Phases 3-5b (apt/git/pip/conda):** Fail-fast. The first error triggers `handle_failure()` and exits with code 1.
- **Phase 6 (downloads):** Best-effort. Individual failures are logged and `had_errors` is set, but all downloads are attempted.
- **Phases 7-7b-8 (services/write_files_late/post_commands):** Always run. Failures set `had_errors` but don't prevent other services or commands from completing.

After all phases, if `had_errors` is true, `handle_failure()` is called with the configured `on_failure` action.

The provisioner's exit code is checked by the boot script (`/etc/vast_boot.d/`). A non-zero exit means provisioning is incomplete and will be retried on next boot (the `/.provisioning_manifest_complete` sentinel is not written).

## CLI Reference

```
provisioner <manifest> [--dry-run] [--force]
```

| Argument | Description |
|----------|-------------|
| `manifest` | Path to YAML manifest file (required) |
| `--dry-run` | Print what would be done without executing anything |
| `--force` | Clear all cached state and re-run every phase |

The `bin/provisioner` wrapper script activates the provisioner's own venv and sets `PYTHONPATH`, so the command is available directly:

```bash
provisioner /path/to/manifest.yaml --dry-run
```

## Running Tests

```bash
cd /opt/instance-tools

# Run all tests
PYTHONPATH=lib python3 -m pytest lib/provisioner/tests/ -v

# Run a specific test file
PYTHONPATH=lib python3 -m pytest lib/provisioner/tests/test_schema.py -v

# Run tests matching a pattern
PYTHONPATH=lib python3 -m pytest lib/provisioner/tests/ -v -k "test_conda"
```

Tests use `unittest.mock` to avoid real subprocess calls, network requests, and filesystem side effects. No external services, tokens, or root permissions are needed.

## Examples

See the [`examples/`](examples/) directory:

| File | Description |
|------|-------------|
| [`minimal.yaml`](examples/minimal.yaml) | Single app, no downloads |
| [`comfyui.yaml`](examples/comfyui.yaml) | ComfyUI with custom nodes and model downloads |
| [`whisper-webui.yaml`](examples/whisper-webui.yaml) | Multi-service (WebUI + API) |
| [`multi-service.yaml`](examples/multi-service.yaml) | Backend + frontend with conda, system pip, retry |
| [`gated-models.yaml`](examples/gated-models.yaml) | Conditional downloads with token gating and env_merge |
| [`full-reference.yaml`](examples/full-reference.yaml) | Every field documented with inline comments |
