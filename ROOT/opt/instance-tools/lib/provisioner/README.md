# Provisioner

A declarative, YAML-driven instance provisioner for Vast.ai base images. Instead of writing imperative shell scripts, you describe *what* should be installed in a manifest file and the provisioner handles execution order, parallelism, idempotency, and failure handling.

```
provisioner [manifest.yaml | URL] [--dry-run] [--force]
```

The manifest source can be a local file path or an HTTP(S) URL. When given a URL, the provisioner downloads the manifest internally (with proper error handling and retry support) before processing it.

The manifest argument is optional. If omitted, the provisioner checks for `PROVISIONING_SCRIPT` and runs only that (script-only mode). If neither a manifest nor `PROVISIONING_SCRIPT` is set, it exits 0 silently.

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
  - [extensions](#extensions)
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
├── extensions.py                   Extension loader and runner (phase 1b)
├── state.py                        Content-hash idempotency (/.provisioner_state/)
├── failure.py                      Failure actions (continue/destroy/stop)
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
| 1b | Run extensions (append to manifest) | **Fail-fast** |
| 2 | Validate auth tokens, resolve conditionals, apply env_merge | Non-fatal |
| 2b | Write early files (`write_files`) | **Fail-fast** |
| 3 | Install apt packages | **Fail-fast** |
| 4 | Clone git repos + run post_commands (parallel) | **Fail-fast** |
| 5 | Install pip packages (per block, sequential) | **Fail-fast** |
| 5b | Install conda packages | **Fail-fast** |
| 6 | Download files (parallel, two pools: HF + wget) | **Fail-fast** |
| 7 | Register supervisor services | **Fail-fast** |
| 7b | Write late files (`write_files_late`) | **Fail-fast** |
| 8 | Run post_commands | **Fail-fast** |
| 9 | Run `PROVISIONING_SCRIPT` (legacy script) | **Fail-fast** |

**Fail-fast** means a failure in that phase skips all remaining phases and exits immediately with code 1. If provisioning hasn't produced the intended environment, it is marked as failed so the retry loop can re-attempt.

The exit code is 0 on full success, 1 if anything failed. A non-zero exit tells the boot script not to mark provisioning as complete, so it will be retried on next restart.

## Manifest Reference

Every manifest starts with `version: 1`. All other sections are optional.

### settings

```yaml
settings:
  venv: "/venv/main"
  conda_env: ""                    # default conda prefix env (empty = base environment)
  log_file: "/var/log/portal/provisioning.log"
  concurrency:
    hf_downloads: 3
    wget_downloads: 5
  retry:
    max_attempts: 5
    initial_delay: 2          # seconds before first retry
    backoff_multiplier: 2     # exponential backoff (delays: 2s, 4s, 8s, 16s)
```

All values shown are defaults. `venv` is the default target for pip installs when no block-level venv is specified. `conda_env` is the default target for conda installs when no block-level `env` is specified (empty means the base/active conda environment). `retry` controls download retry behavior.

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
  action: continue        # continue | destroy | stop
  max_retries: 3          # retry attempts before running action (default: 3)
  retry_delay: 30         # seconds between retries (default: 30)
  webhook: ""             # URL to POST failure/success info (optional)
  webhook_on_success: false  # also fire webhook on successful provisioning (default: false)
```

The provisioner always retries on failure — retries are built into the provisioner's run loop (sleep + re-run, up to `max_retries` times). The `action` controls what happens **after all retries are exhausted**:

| Action | Behavior |
|--------|----------|
| `continue` | Log the failure, exit 1. Default. |
| `destroy` | Call `vastai destroy instance $CONTAINER_ID --api-key $CONTAINER_API_KEY`. |
| `stop` | Call `vastai stop instance $CONTAINER_ID --api-key $CONTAINER_API_KEY`. Uses `/.provisioner_stopped` sentinel to prevent restart loops. |

Set `max_retries: 0` to skip the retry loop and go straight to the action on first failure.

**Webhook:** If configured, a JSON payload is POSTed after retries are exhausted (and on success if `webhook_on_success: true`):

```json
{
  "action": "continue",
  "manifest": "/path/to/manifest.yaml",
  "error": "provisioning failed after all retries",
  "container_id": "12345",
  "timestamp": "2025-01-15T10:30:00"
}
```

**Environment variable overrides:** All `PROVISIONER_*` env vars override manifest values, letting operators tune behavior without editing manifests:

| Env var | Overrides | Notes |
|---------|-----------|-------|
| `PROVISIONER_RETRY_MAX` | `on_failure.max_retries` | Override retry count |
| `PROVISIONER_RETRY_DELAY` | `on_failure.retry_delay` | Override delay between retries |
| `PROVISIONER_FAILURE_ACTION` | `on_failure.action` | Force action across fleet |
| `PROVISIONER_WEBHOOK_URL` | `on_failure.webhook` | Global webhook URL |
| `PROVISIONER_WEBHOOK_ON_SUCCESS` | `on_failure.webhook_on_success` | `1`/`true`/`yes` to enable |
| `PROVISIONER_LOG_FILE` | `settings.log_file` | Redirect logs per-instance |
| `PROVISIONER_VENV` | `settings.venv` | Different default venv per deployment |
| `PROVISIONER_CONDA_ENV` | `settings.conda_env` | Different default conda env per deployment |

### write_files / write_files_late

Cloud-init style file writing in two phases: `write_files` runs early (phase 2b, fail-fast) and `write_files_late` runs late (phase 7b, fail-fast).

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

### extensions

Run custom Python modules in phase 1b, immediately after the manifest is loaded. Extensions exist solely to **append items** to the manifest's lists (`manifest.downloads`, `manifest.git_repos`, `manifest.pip_packages`, etc.). They do not perform any installation, downloading, or cloning themselves — the normal provisioner phases handle all of that.

A concrete use case is the `provisioner_comfyui` extension (see below), which parses ComfyUI workflow JSON files to discover required models and custom nodes, then appends them to `manifest.downloads` and `manifest.git_repos` for phases 4 and 6 to handle.

This allows derivative images to add image-specific discovery logic without modifying the base provisioner.

```yaml
extensions:
  - module: provisioner_comfyui           # Importable Python module name
    config:                                # Arbitrary dict passed to extension
      workflows:
        - https://example.com/workflow.json
        - /workspace/workflows/my_workflow.json
  - module: provisioner_other
    enabled: false                         # Skip this extension
```

| Field | Default | Description |
|-------|---------|-------------|
| `module` | `""` | Python module name (must be importable via `PYTHONPATH`) |
| `config` | `{}` | Arbitrary configuration dict passed to the extension's `run()` function |
| `enabled` | `true` | Set to `false` to skip this extension |

**Extension interface:** Each module must implement a `run()` function:

```python
def run(config: dict, context: ExtensionContext, dry_run: bool = False) -> None:
    # Discover resources and append to the manifest
    for wf in config.get("workflows", []):
        models, nodes = parse_workflow(wf)
        context.manifest.downloads.extend(models)
        context.manifest.git_repos.extend(nodes)
```

`ExtensionContext` provides:
- `manifest` — the full `Manifest` object. Extensions append to its lists (`downloads`, `git_repos`, `pip_packages`, etc.) to inject discovered resources into later phases.
- `log` — a logger instance for the extension

**Module placement:** The `bin/provisioner` wrapper sets `PYTHONPATH=/opt/instance-tools/lib`. Derivative images place extension modules under `/opt/instance-tools/lib/` (e.g. `/opt/instance-tools/lib/provisioner_comfyui/__init__.py`). No pip install required — just drop in the package.

**Error handling:** Extension failure is **fail-fast** — if an extension raises, the provisioner aborts before any other phase runs. This prevents partial provisioning when discovery fails.

**Built-in extension — `provisioner_comfyui`:** Parses ComfyUI workflow JSON files (GUI format) to automatically discover required models and custom nodes. For each workflow URL, it downloads the JSON, extracts model download URLs from `nodes[].properties.models[]`, resolves custom node git repos via the [ComfyUI Registry API](https://registry.comfy.org), and appends them to `manifest.downloads` and `manifest.git_repos`. Workflows are also saved to `{comfyui_dir}/user/default/workflows/` via `write_files_late`.

```yaml
extensions:
  - module: provisioner_comfyui
    config:
      workflows:
        - https://example.com/my-workflow.json
      comfyui_dir: "${WORKSPACE:-/workspace}/ComfyUI"  # optional, this is the default
```

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

A list of install blocks. Each block can target a different conda prefix environment.

```yaml
conda_packages:
  - packages:
      - "cudatoolkit=11.8"
      - "nccl>=2.18"
    channels:
      - conda-forge
      - nvidia
    env: "/venv/conda-ml"          # target conda prefix env (auto-created)
    python: "3.11"                  # for env auto-creation
    args: "--no-update-deps"

  - packages:
      - "ffmpeg"
    channels:
      - conda-forge
    env: "/venv/conda-media"       # different env
```

Uses the Miniforge3 installation at `/opt/miniforge3/`. Prefers `mamba` (faster dependency solver), falls back to `conda`. The tool is located by checking `/opt/miniforge3/bin/` first, then `PATH`.

| Field | Default | Description |
|-------|---------|-------------|
| `packages` | `[]` | Package specifiers (conda syntax: `=` exact, `>=`, `<=`, `<`, `>`) |
| `channels` | `[]` | Extra channels passed as `-c` flags |
| `env` | `""` | Target conda prefix environment path |
| `python` | `""` | Python version for env auto-creation |
| `args` | `""` | Extra arguments |

Without `env`, the `settings.conda_env` default is used. If that is also empty, packages install into the base/active conda environment. With `env`, the prefix is auto-created if the `conda-meta/` directory doesn't exist, using `{tool} create -y -p {path} [python={version}]`.

**Backward compatibility:** A single dict (old format) is automatically wrapped in a list:
```yaml
# This still works:
conda_packages:
  packages: [numpy]
```

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
  # Single file download to specific path
  - url: https://huggingface.co/org/model/resolve/main/weights.safetensors
    dest: /workspace/models/weights.safetensors

  # Full repo download to a directory (for inference engines, LLMs, etc.)
  - url: https://huggingface.co/meta-llama/Llama-3-8B
    dest: /workspace/models/llama3

  # Full repo download to HF cache ($HF_HOME) -- no dest needed
  # vLLM and other engines can load directly from cache
  - url: https://huggingface.co/meta-llama/Llama-3-8B

  # CivitAI and generic downloads
  - url: https://civitai.com/api/download/models/12345
    dest: /workspace/models/loras/        # trailing / = resolve filename from server
  - url: https://example.com/file.bin
    dest: /workspace/other/file.bin
```

Downloads run in two independent parallel pools:

| URL Pattern | Handler | Auth | Pool |
|-------------|---------|------|------|
| `huggingface.co` | `hf download` | `$HF_TOKEN` (automatic) | `hf_downloads` |
| `civitai.com` | `wget` with `Authorization` header | `$CIVITAI_TOKEN` | `wget_downloads` |
| Everything else | `wget` | None | `wget_downloads` |

**HuggingFace URL formats:**

| URL | Behavior |
|-----|----------|
| `.../resolve/{rev}/{file}` with `dest` | Download single file to specific path |
| `.../resolve/{rev}/{file}` without `dest` | Download single file to HF cache |
| `huggingface.co/org/repo` with `dest` | Download full repo to directory (`--local-dir`) |
| `huggingface.co/org/repo` without `dest` | Download full repo to HF cache (`$HF_HOME`) |

**HF cache mode** (no `dest`): `hf download` manages its own cache at `$HF_HOME` (default `~/.cache/huggingface/hub`). This is the standard approach for inference engines like vLLM that load models from cache by repo ID. The cache handles deduplication, symlinks, and resumption automatically.

**Trailing `/` on dest:** For wget downloads, the provisioner resolves the filename from the server's `Content-Disposition` header (falling back to the URL's last path segment) and appends it to the directory path. For HF single-file downloads, the filename is taken from the URL.

**Features:**
- Parallel downloads with configurable pool sizes
- `fcntl`-based file locking prevents concurrent downloads of the same file
- Retry with exponential backoff on failure
- Existing files are skipped (checked inside the lock to prevent races)
- HuggingFace single-file downloads use a temp directory and atomic move to dest
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

Arbitrary shell commands run sequentially after all other phases. Each command runs with `shell=True`. A failed command aborts immediately (fail-fast) — subsequent commands do not run. Use this for npm installs, symlinks, config generation, or anything not covered by other sections.

### PROVISIONING_SCRIPT (Phase 9)

Legacy shell script support. If the `PROVISIONING_SCRIPT` environment variable is set, the provisioner downloads (if URL) and executes it as the final phase after all manifest phases complete.

```bash
# Set via environment variable (not in the manifest)
PROVISIONING_SCRIPT=https://example.com/setup.sh
```

**Behavior:**
- If the value is an HTTP(S) URL, it is downloaded to `/provisioning.sh` (same retry logic as manifest URL downloads)
- `dos2unix` is run on the script (if available) to handle Windows line endings
- The script is made executable (`chmod +x`) and executed
- stdout/stderr are captured and logged through the provisioner's logging system
- A non-zero exit code is treated as a failure (fail-fast)
- Content-hash idempotency: the script URL is hashed, so re-runs with the same URL skip the phase

**Script-only mode:** If no manifest is provided but `PROVISIONING_SCRIPT` is set, the provisioner creates a default configuration and runs only Phase 9. The default retry/failure settings can be overridden with `PROVISIONER_*` environment variables (e.g. `PROVISIONER_RETRY_MAX`, `PROVISIONER_FAILURE_ACTION`).

This replaces the legacy `75-provisioning-script.sh` boot script. Existing scripts work without modification — they gain retries, failure actions, webhook notifications, and unified logging automatically.

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
| extensions | Ordered (module, config, enabled) dicts |
| git | Sorted (url, ref, recursive, pull_if_exists, post_commands) tuples |
| pip | Per-block: (venv, tool, packages, args, requirements, python) |
| conda | Per-block: (env, packages, channels, args, python) |
| downloads | Sorted (url, dest) tuples |
| services | Sorted (name, command, venv, workdir, environment) tuples |
| write_files_late | Ordered (path, content, permissions, owner) tuples |
| post_commands | Ordered command list |

**`--force`** clears all stored hashes, forcing every phase to re-run.

**`--dry-run`** never reads or writes state. All phases execute in dry-run mode regardless of stored hashes. **Note:** Extensions are skipped entirely in dry-run mode, so any items they would append to the manifest (downloads, git repos, pip packages, etc.) will not appear in the dry-run output.

**Edge cases:**
- The git hash covers repo configuration, not repo contents. Manually editing a cloned repo doesn't invalidate the hash. Set `pull_if_exists: true` if you need to force updates.
- The download stage hash covers the URL list. Individual file-exists checks still run within the phase, so already-downloaded files are skipped even when the phase re-runs.
- Download and post_commands hashes are only stored when no errors occurred in those phases.
- State lives at `/.provisioner_state/` (root filesystem), surviving workspace resets but wiped on container rebuild.

## Failure Handling

All phases are **fail-fast**: the first error in any phase exits the current run attempt with code 1, skipping all remaining phases. If provisioning hasn't produced the intended environment, it is marked as failed.

**Retry loop:** When `run()` returns non-zero, the provisioner sleeps `retry_delay` seconds and re-runs the entire pipeline (up to `max_retries` times). Because phases are idempotent (content-hash checked), previously successful phases are skipped on retry — only the failed phase and later phases re-execute.

After all retries are exhausted, `handle_failure()` is called with the configured `on_failure.action` (continue/destroy/stop).

**Stop-once safety:** The `stop` action writes a sentinel file (`/.provisioner_stopped`) before stopping the instance. On subsequent boots, if the sentinel exists, the stop is skipped to prevent a restart loop (provisioner fails → stops → user restarts → provisioner fails → stops again). The `destroy` action does not use the sentinel since a destroyed instance cannot restart.

The provisioner's exit code is checked by the boot script (`/etc/vast_boot.d/`). A non-zero exit means provisioning is incomplete and will be retried on next boot (the `/.provisioning_complete` sentinel is not written).

## CLI Reference

```
provisioner [manifest.yaml | URL] [--dry-run] [--force]
```

| Argument | Description |
|----------|-------------|
| `manifest` | Path to YAML manifest file **or** HTTP(S) URL (optional if `PROVISIONING_SCRIPT` is set) |
| `--dry-run` | Print what would be done without executing anything |
| `--force` | Clear all cached state and re-run every phase |

The `bin/provisioner` wrapper script activates the provisioner's own venv and sets `PYTHONPATH`, so the command is available directly:

```bash
# Local file
provisioner /path/to/manifest.yaml --dry-run

# Remote URL
provisioner https://example.com/manifest.yaml --dry-run
```

When given a URL, the manifest is downloaded to `/provisioning.yaml` before processing. Download failures are handled by the provisioner's retry and failure machinery (webhooks, stop/destroy actions, etc.).

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
| [`integration-test.yaml`](examples/integration-test.yaml) | Real repos and models for live validation |
| [`full-reference.yaml`](examples/full-reference.yaml) | Every field documented with inline comments |
