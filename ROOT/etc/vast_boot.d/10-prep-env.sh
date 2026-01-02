#!/bin/bash

mkdir -p "${WORKSPACE}"
cd "${WORKSPACE}"

# CUDA Forward Compatibility: datacenter GPUs with Volta+ and compat libs present
# Consumer class GPUs can rely on minor version compatibility, but forward compatibility must be removed first

configure_cuda() {
    command -v nvidia-smi &> /dev/null || return 0

    # Clean up ALL cuda ldconfig entries - we'll add back only what we need
    # Remove cuda-specific conf files
    rm -f /etc/ld.so.conf.d/*cuda*.conf

    # Remove any cuda references from ALL remaining conf files
    for conf in /etc/ld.so.conf.d/*.conf; do
        [[ -f "$conf" ]] || continue
        if grep -q "cuda" "$conf" 2>/dev/null; then
            sed -i '\#cuda#d' "$conf"
            # Remove file if now empty
            [[ ! -s "$conf" ]] && rm -f "$conf"
        fi
    done

    # Also clean main ld.so.conf
    sed -i '\#cuda#d' /etc/ld.so.conf 2>/dev/null

    # Refresh ldconfig BEFORE querying nvidia-smi
    ldconfig

    if [[ -n "$LD_LIBRARY_PATH" ]]; then
        LD_LIBRARY_PATH=$(echo "$LD_LIBRARY_PATH" | tr ':' '\n' | grep -v "cuda" | paste -sd ':')
        [[ -n "$LD_LIBRARY_PATH" ]] && export LD_LIBRARY_PATH || unset LD_LIBRARY_PATH
    fi

    # Gather GPU info
    local GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)
    local CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n1)
    local DRIVER_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)
    local DRIVER_MAJOR=${DRIVER_VER%%.*}
    local MAX_CUDA=$(nvidia-smi | grep -oP "CUDA Version: \K[0-9]+\.[0-9]+")

    if [[ -z "$MAX_CUDA" ]]; then
        echo "Error: Could not determine driver CUDA version"
        return 1
    fi

    # Find all installed CUDA versions, sorted descending
    local CUDA_VERSIONS=()
    for dir in /usr/local/cuda-*/; do
        [[ -d "$dir" ]] || continue
        local ver=$(basename "$dir" | sed 's/cuda-//')
        [[ "$ver" =~ ^[0-9]+\.[0-9]+$ ]] && CUDA_VERSIONS+=("$ver")
    done
    IFS=$'\n' CUDA_VERSIONS=($(sort -t. -k1,1nr -k2,2nr <<<"${CUDA_VERSIONS[*]}")); unset IFS

    [[ ${#CUDA_VERSIONS[@]} -eq 0 ]] && return 0

    local IS_DATACENTER=false
    if [[ ! "$GPU_NAME" =~ (RTX|GeForce|Quadro|Titan) ]] &&
       [[ "$GPU_NAME" =~ (V100|T4|A[0-9]+|H[0-9]+|L[0-9]+|B[0-9]+) ]] &&
       awk "BEGIN {exit !(${CC:-0} >= 7.0)}"; then
        IS_DATACENTER=true
    fi

    local SELECTED_CUDA=""
    local ENABLE_COMPAT=false
    local COMPAT_DIR=""

    if $IS_DATACENTER; then
        # Datacenter: use latest CUDA, enable forward compat if needed
        SELECTED_CUDA="${CUDA_VERSIONS[0]}"
        COMPAT_DIR="/usr/local/cuda-${SELECTED_CUDA}/compat"
        
        if [[ -d "$COMPAT_DIR" ]] && compgen -G "$COMPAT_DIR/libcuda.so.*" > /dev/null; then
            if [[ -e "$COMPAT_DIR/libcuda.so.1" ]]; then
                local COMPAT_REALPATH=$(readlink -f "$COMPAT_DIR/libcuda.so.1")
                local COMPAT_LIB=$(basename "$COMPAT_REALPATH")
                local COMPAT_SUFFIX=${COMPAT_LIB#libcuda.so.}
                local COMPAT_MAJOR=""
                
                if [[ "$COMPAT_SUFFIX" =~ ^([0-9]+)\.[0-9]+ ]]; then
                    COMPAT_MAJOR=${BASH_REMATCH[1]}
                fi

                if [[ -n "$COMPAT_MAJOR" && -n "$DRIVER_MAJOR" ]] &&
                   (( DRIVER_MAJOR < COMPAT_MAJOR )); then
                    ENABLE_COMPAT=true
                    echo "CUDA forward compatibility enabled (driver: $DRIVER_MAJOR, compat: $COMPAT_MAJOR)"
                fi
            fi
        fi
    else
        # Consumer: highest installed CUDA that doesn't exceed driver support
        for ver in "${CUDA_VERSIONS[@]}"; do
            if awk "BEGIN {exit !($ver <= $MAX_CUDA)}"; then
                SELECTED_CUDA="$ver"
                break
            fi
        done

        # Fallback to lowest available if nothing matches
        if [[ -z "$SELECTED_CUDA" ]]; then
            SELECTED_CUDA="${CUDA_VERSIONS[-1]}"
            echo "Warning: Driver reports CUDA $MAX_CUDA but no compatible toolkit found; falling back to CUDA $SELECTED_CUDA"
        fi
    fi

    if [[ -n "$SELECTED_CUDA" ]]; then
        export CUDA_HOME="/usr/local/cuda-${SELECTED_CUDA}"
        export PATH="${CUDA_HOME}/bin:${PATH}"
        
        # Update /usr/local/cuda symlink
        rm -f /usr/local/cuda
        ln -sf "cuda-${SELECTED_CUDA}" /usr/local/cuda

        # Compat libs - processed first (00 prefix)
        echo "$COMPAT_DIR" > /etc/ld.so.conf.d/00-cuda-compat.conf
        
        # Register only the selected CUDA's libraries
        {
            echo "${CUDA_HOME}/targets/x86_64-linux/lib"
            echo "${CUDA_HOME}/lib64"
        } > /etc/ld.so.conf.d/50-cuda.conf

        echo "CUDA $SELECTED_CUDA selected (GPU: $GPU_NAME, CC: $CC, Driver: $DRIVER_VER, Max CUDA: $MAX_CUDA, Datacenter: $IS_DATACENTER)"
    fi

    ldconfig
}

configure_cuda

# Avoid missing cuda libs error (affects 12.4)
if [[ ! -e /usr/lib/x86_64-linux-gnu/libcuda.so && -e /usr/lib/x86_64-linux-gnu/libcuda.so.1 ]]; then \
    ln -s /usr/lib/x86_64-linux-gnu/libcuda.so.1 /usr/lib/x86_64-linux-gnu/libcuda.so; \
fi

# Fix pip resolution if we moved it in the build to protect system python
[ -x "$(command -v pip-v-real)" ] && mv "$(which pip-v-real)" "$(dirname "$(which pip-v-real)")/pip"
[ -x "$(command -v pip3-v-real)" ] && mv "$(which pip3-v-real)" "$(dirname "$(which pip3-v-real)")/pip3"

# Remove Jupyter from the portal config if no port or running in SSH only mode
if [[ -z "${VAST_TCP_PORT_8080}" ]] || { [[ -f /.launch ]] && ! grep -qi jupyter /.launch && [[ "${JUPYTER_OVERRIDE,,}" != "true" ]]; }; then
    PORTAL_CONFIG=$(echo "$PORTAL_CONFIG" | tr '|' '\n' | grep -vi jupyter | tr '\n' '|' | sed 's/|$//')
fi

# Ensure correct port mappings for Jupyter when running in Jupyter launch mode
if [[ -f /.launch ]] && grep -qi jupyter /.launch && [[ "${JUPYTER_OVERRIDE,,}" != "true" ]]; then
    PORTAL_CONFIG="$(echo "$PORTAL_CONFIG" | sed 's#localhost:8080:18080#localhost:8080:8080#g')"
fi

# Set HuggingFace home
export HF_HOME=${HF_HOME:-${WORKSPACE}/.hf_home}
mkdir -p "$HF_HOME"

# Ensure environment contains instance ID (snapshot aware)
instance_identifier=$(echo "${CONTAINER_ID:-${VAST_CONTAINERLABEL:-${CONTAINER_LABEL:-}}}")
message="# Template controlled environment for C.${instance_identifier}"
if [[ -z "${instance_identifier:-}" ]] || ! grep -q "$message" /etc/environment; then
    echo "$message" > /etc/environment
    echo 'PATH="/opt/instance-tools/bin:/usr/local/nvidia/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"' \
        >> /etc/environment
    env -0 | grep -zEv "^(HOME=|SHLVL=)|CONDA" | while IFS= read -r -d '' line; do
            name=${line%%=*}
            value=${line#*=}
            printf '%s="%s"\n' "$name" "$value"
        done >> /etc/environment
fi

# Source the file at /etc/environment - We can now edit environment variables in a running instance
[[ "${export_env}" = "true" ]] && { set -a; . /etc/environment 2>/dev/null; . "${WORKSPACE}/.env" 2>/dev/null; set +a; }
