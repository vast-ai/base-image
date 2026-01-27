#!/bin/bash

set -euo pipefail

### Configuration ###
WORKSPACE_DIR="${WORKSPACE:-/workspace}"
FORGE_DIR="${WORKSPACE_DIR}/stable-diffusion-webui-forge"
MODELS_DIR="${FORGE_DIR}/models"
SEMAPHORE_DIR="${WORKSPACE_DIR}/download_sem_$$"
MAX_PARALLEL="${MAX_PARALLEL:-3}"
PROVISIONING_LOG="${PROVISIONING_LOG:-/var/log/portal/forge.log}"

# APT packages to install (uncomment as needed)
APT_PACKAGES=(
    #"package-1"
    #"package-2"
)

# Python packages to install (uncomment as needed)
PIP_PACKAGES=(
    #"package-1"
    #"package-2"
)

# Extensions to install: "REPO_URL"
# Can also be set via EXTENSIONS env var (semicolon-separated)
# Example: EXTENSIONS="https://github.com/org/ext1;https://github.com/org/ext2"
EXTENSIONS=(
    #"https://github.com/example/extension-name"
)

# Model downloads use "URL|OUTPUT_PATH" format
# - If OUTPUT_PATH ends with /, filename is extracted via content-disposition
# - Can also be set via environment variables (semicolon-separated entries)
#
# Example env var format:
#   HF_MODELS="https://huggingface.co/org/repo/resolve/main/model.safetensors|/workspace/models/model.safetensors;https://huggingface.co/org/repo2/resolve/main/model2.safetensors|/workspace/models/model2.safetensors"
#   CIVITAI_MODELS="https://civitai.com/api/download/models/12345|/workspace/models/Stable-diffusion/"
#   WGET_DOWNLOADS="https://example.com/file.bin|/workspace/files/file.bin"

# HuggingFace models (requires HF_TOKEN for gated models)
HF_MODELS_DEFAULT=(
    #"https://huggingface.co/stabilityai/sd-vae-ft-mse-original/resolve/main/vae-ft-mse-840000-ema-pruned.safetensors
    #|$MODELS_DIR/VAE/vae-ft-mse-840000-ema-pruned.safetensors"
)

# CivitAI models (requires CIVITAI_TOKEN for some models)
# Use trailing / for output path to use content-disposition filename
CIVITAI_MODELS_DEFAULT=(
    "https://civitai.com/api/download/models/798204?type=Model&format=SafeTensor&size=full&fp=fp16
    |$MODELS_DIR/Stable-diffusion/"
)

# Generic wget downloads (no auth)
WGET_DOWNLOADS_DEFAULT=(
    #"https://example.com/file.safetensors|$MODELS_DIR/other/file.safetensors"
)

### End Configuration ###

# Ensure log directory exists
mkdir -p "$(dirname "$PROVISIONING_LOG")"

log() {
    local message="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$message" | tee -a "$PROVISIONING_LOG"
}

script_cleanup() {
    log "Cleaning up semaphore directory..."
    rm -rf "$SEMAPHORE_DIR"
    # Clean up any stale lock files from this run
    find "$MODELS_DIR" -name "*.lock" -type f -mmin +60 -delete 2>/dev/null || true
}

script_error() {
    local exit_code=$?
    local line_number=$1
    log "[ERROR] Provisioning script failed at line $line_number with exit code $exit_code"
    exit "$exit_code"
}

trap script_cleanup EXIT
trap 'script_error $LINENO' ERR

# Parse semicolon-separated string into array
# Usage: parse_env_array "ENV_VAR_NAME" "default_array_name"
parse_env_array() {
    local env_var_name="$1"
    local env_value="${!env_var_name:-}"

    if [[ -n "$env_value" ]]; then
        local -a result=()
        IFS=';' read -ra entries <<< "$env_value"
        for entry in "${entries[@]}"; do
            # Trim whitespace
            entry=$(echo "$entry" | xargs)
            [[ -n "$entry" ]] && result+=("$entry")
        done
        # Return array elements, one per line
        printf '%s\n' "${result[@]}"
    fi
}

# Merge default array with environment variable overrides
# Usage: merged_array=($(merge_with_env "ENV_VAR" default_array))
merge_with_env() {
    local env_var_name="$1"
    shift
    local -a default_array=("$@")
    local env_value="${!env_var_name:-}"

    if [[ -n "$env_value" ]]; then
        # Environment variable takes precedence - parse it
        parse_env_array "$env_var_name"
    else
        # Use defaults
        printf '%s\n' "${default_array[@]}"
    fi
}

acquire_slot() {
    local prefix="$1"
    local max_slots="$2"

    while true; do
        local count
        count=$(find "$(dirname "$prefix")" -name "$(basename "$prefix")_*" 2>/dev/null | wc -l)
        if [ "$count" -lt "$max_slots" ]; then
            local slot="${prefix}_$$_$RANDOM"
            touch "$slot"
            echo "$slot"
            return 0
        fi
        sleep 0.5
    done
}

release_slot() {
    rm -f "$1"
}

# Check if HF token is valid
has_valid_hf_token() {
    [[ -n "${HF_TOKEN:-}" ]] || return 1
    local response
    response=$(curl -o /dev/null -s -w "%{http_code}" -X GET \
        "https://huggingface.co/api/whoami-v2" \
        -H "Authorization: Bearer $HF_TOKEN" \
        -H "Content-Type: application/json")
    [[ "$response" -eq 200 ]]
}

# Check if CivitAI token is valid
has_valid_civitai_token() {
    [[ -n "${CIVITAI_TOKEN:-}" ]] || return 1
    local response
    response=$(curl -o /dev/null -s -w "%{http_code}" -X GET \
        "https://civitai.com/api/v1/models?hidden=1&limit=1" \
        -H "Authorization: Bearer $CIVITAI_TOKEN" \
        -H "Content-Type: application/json")
    [[ "$response" -eq 200 ]]
}

# Download a file with retry logic and proper locking
# Args: URL OUTPUT_PATH [AUTH_TYPE]
# AUTH_TYPE: "hf", "civitai", or empty for no auth
# If OUTPUT_PATH ends with /, uses content-disposition for filename
download_file() {
    local url="$1"
    local output_path="$2"
    local auth_type="${3:-}"
    local max_retries=5
    local retry_delay=2

    # Acquire slot for parallel download limiting
    local slot
    slot=$(acquire_slot "$SEMAPHORE_DIR/dl" "$MAX_PARALLEL")

    # Determine if output is a directory (use content-disposition) or full path
    local output_dir output_file use_content_disposition=false
    if [[ "$output_path" == */ ]]; then
        output_dir="${output_path%/}"
        use_content_disposition=true
    else
        output_dir="$(dirname "$output_path")"
        output_file="$(basename "$output_path")"
    fi

    mkdir -p "$output_dir"

    # Build auth header based on auth_type
    local auth_header=""
    if [[ "$auth_type" == "hf" ]] && [[ -n "${HF_TOKEN:-}" ]]; then
        auth_header="Authorization: Bearer $HF_TOKEN"
    elif [[ "$auth_type" == "civitai" ]] && [[ -n "${CIVITAI_TOKEN:-}" ]]; then
        auth_header="Authorization: Bearer $CIVITAI_TOKEN"
    fi

    local lockfile="${output_dir}/.download_${RANDOM}.lock"

    (
        # Acquire exclusive lock (wait up to 300 seconds)
        if ! flock -x -w 300 200; then
            log "[ERROR] Could not acquire lock for download after 300s"
            release_slot "$slot"
            exit 1
        fi

        local attempt=1
        local current_delay=$retry_delay

        while [ $attempt -le $max_retries ]; do
            log "Downloading: $url (attempt $attempt/$max_retries)..."

            local wget_args=(
                --timeout=60
                --tries=1
                --progress=dot:giga
            )

            if [[ -n "$auth_header" ]]; then
                wget_args+=(--header="$auth_header")
            fi

            if [[ "$use_content_disposition" == true ]]; then
                wget_args+=(--content-disposition -P "$output_dir")
            else
                # Check if file already exists
                if [[ -f "$output_dir/$output_file" ]]; then
                    log "File already exists: $output_dir/$output_file (skipping)"
                    release_slot "$slot"
                    exit 0
                fi
                wget_args+=(-O "$output_dir/$output_file")
            fi

            if wget "${wget_args[@]}" "$url" 2>&1 | tee -a "$PROVISIONING_LOG"; then
                release_slot "$slot"
                log "Successfully downloaded to: $output_dir"
                exit 0
            fi

            log "Download failed (attempt $attempt/$max_retries), retrying in ${current_delay}s..."
            sleep $current_delay
            current_delay=$((current_delay * 2))
            attempt=$((attempt + 1))
        done

        log "[ERROR] Failed to download $url after $max_retries attempts"
        release_slot "$slot"
        exit 1

    ) 200>"$lockfile"

    local result=$?
    rm -f "$lockfile"
    return $result
}

# Install APT packages
install_apt_packages() {
    if [[ ${#APT_PACKAGES[@]} -gt 0 && -n "${APT_PACKAGES[*]}" ]]; then
        log "Installing APT packages..."
        sudo apt-get update
        sudo apt-get install -y "${APT_PACKAGES[@]}"
    fi
}

# Install Python packages
install_pip_packages() {
    if [[ ${#PIP_PACKAGES[@]} -gt 0 && -n "${PIP_PACKAGES[*]}" ]]; then
        log "Installing Python packages..."
        uv pip install --no-cache-dir "${PIP_PACKAGES[@]}"
    fi
}

# Install extensions
install_extensions() {
    # Merge defaults with env var
    local -a extensions=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && extensions+=("$line")
    done < <(merge_with_env "EXTENSIONS" "${EXTENSIONS[@]}")

    if [[ ${#extensions[@]} -eq 0 ]]; then
        return 0
    fi

    # Avoid git errors because we run as root but files are owned by 'user'
    export GIT_CONFIG_GLOBAL=/tmp/temporary-git-config
    git config --file "$GIT_CONFIG_GLOBAL" --add safe.directory '*'

    for repo in "${extensions[@]}"; do
        [[ -z "$repo" || "$repo" == \#* ]] && continue

        local dir="${repo##*/}"
        dir="${dir%.git}"
        local path="${FORGE_DIR}/extensions/${dir}"

        if [[ -d "$path" ]]; then
            log "Extension already installed: $dir"
        else
            log "Installing extension: $repo"
            git clone "$repo" "$path" --recursive
        fi
    done
}

# Download models from an array with specified auth type
# Args: array_name auth_type
download_models() {
    local -n model_array=$1
    local auth_type="$2"
    local pids=()

    for entry in "${model_array[@]}"; do
        # Skip empty or commented entries
        [[ -z "${entry// }" || "$entry" == \#* ]] && continue

        local url="${entry%%|*}"
        local output_path="${entry##*|}"

        # Trim whitespace
        url=$(echo "$url" | xargs)
        output_path=$(echo "$output_path" | xargs)

        log "Queuing download: $url -> $output_path"
        download_file "$url" "$output_path" "$auth_type" &
        pids+=($!)
    done

    # Wait for all downloads and track failures
    local failed=0
    for pid in "${pids[@]}"; do
        if ! wait "$pid"; then
            log "[ERROR] Download process $pid failed"
            failed=1
        fi
    done

    return $failed
}

# Run Forge startup test to ensure dependencies are ready
run_startup_test() {
    log "Running Forge startup test..."

    # Avoid git errors
    export GIT_CONFIG_GLOBAL=/tmp/temporary-git-config
    git config --file "$GIT_CONFIG_GLOBAL" --add safe.directory '*'

    cd "${FORGE_DIR}"
    LD_PRELOAD=libtcmalloc_minimal.so.4 \
        python launch.py \
            --skip-python-version-check \
            --no-download-sd-model \
            --do-not-download-clip \
            --no-half \
            --port 11404 \
            --exit
}

main() {
    log "========================================"
    log "Starting Forge provisioning..."
    log "========================================"

    # Check for skip flag
    if [[ -f "/.noprovisioning" ]]; then
        log "Provisioning skipped (/.noprovisioning exists)"
        exit 0
    fi

    # Activate virtual environment
    if [[ -f /venv/main/bin/activate ]]; then
        # shellcheck source=/dev/null
        . /venv/main/bin/activate
    fi

    # Validate tokens if set
    if [[ -n "${HF_TOKEN:-}" ]]; then
        if has_valid_hf_token; then
            log "HuggingFace token validated"
        else
            log "[WARN] HF_TOKEN is set but appears invalid"
        fi
    fi

    if [[ -n "${CIVITAI_TOKEN:-}" ]]; then
        if has_valid_civitai_token; then
            log "CivitAI token validated"
        else
            log "[WARN] CIVITAI_TOKEN is set but appears invalid"
        fi
    fi

    # Build model arrays from defaults + env vars
    local -a HF_MODELS=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && HF_MODELS+=("$line")
    done < <(merge_with_env "HF_MODELS" "${HF_MODELS_DEFAULT[@]}")

    local -a CIVITAI_MODELS=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && CIVITAI_MODELS+=("$line")
    done < <(merge_with_env "CIVITAI_MODELS" "${CIVITAI_MODELS_DEFAULT[@]}")

    local -a WGET_DOWNLOADS=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && WGET_DOWNLOADS+=("$line")
    done < <(merge_with_env "WGET_DOWNLOADS" "${WGET_DOWNLOADS_DEFAULT[@]}")

    # Log what we're going to download
    log "HF_MODELS: ${#HF_MODELS[@]} entries"
    log "CIVITAI_MODELS: ${#CIVITAI_MODELS[@]} entries"
    log "WGET_DOWNLOADS: ${#WGET_DOWNLOADS[@]} entries"

    # Clean up any leftover semaphores and create fresh directory
    rm -rf "$SEMAPHORE_DIR"
    mkdir -p "$SEMAPHORE_DIR"

    # Install packages first
    install_apt_packages
    install_pip_packages

    # Install extensions
    install_extensions

    # Download all models in parallel
    local download_failed=0

    log "Starting model downloads..."

    if [[ ${#HF_MODELS[@]} -gt 0 ]]; then
        download_models HF_MODELS "hf" || download_failed=1
    fi

    if [[ ${#CIVITAI_MODELS[@]} -gt 0 ]]; then
        download_models CIVITAI_MODELS "civitai" || download_failed=1
    fi

    if [[ ${#WGET_DOWNLOADS[@]} -gt 0 ]]; then
        download_models WGET_DOWNLOADS "" || download_failed=1
    fi

    if [[ $download_failed -eq 1 ]]; then
        log "[ERROR] One or more downloads failed"
        exit 1
    fi

    log "All downloads completed successfully"

    # Run startup test
    run_startup_test

    log "========================================"
    log "Provisioning complete!"
    log "========================================"
}

main "$@"
