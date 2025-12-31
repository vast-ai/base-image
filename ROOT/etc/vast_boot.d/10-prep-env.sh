#!/bin/bash

mkdir -p "${WORKSPACE}"
cd "${WORKSPACE}"

# CUDA Forward Compatibility: datacenter GPUs with Volta+ and compat libs present
# Consumer class GPUs can rely on minor version compatibility, but forward compatibility must be removed first

if command -v nvidia-smi &> /dev/null; then
    # Remove any baked-in compat lib references first
    grep -l -r "cuda.*/compat" /etc/ld.so.conf.d/ 2>/dev/null | while read -r file; do
        sed -i '\#cuda.*/compat#d' "$file"
    done
    sed -i '\#cuda.*/compat#d' /etc/ld.so.conf 2>/dev/null

    if [[ -n "$LD_LIBRARY_PATH" ]]; then
        LD_LIBRARY_PATH=$(echo "$LD_LIBRARY_PATH" | tr ':' '\n' | grep -v "cuda.*/compat" | paste -sd ':')
        export LD_LIBRARY_PATH
    fi

    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)
    CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n1)

    # Find compat directory - check common locations
    COMPAT_DIR=""
    for dir in /usr/local/cuda/compat /usr/local/cuda-*/compat /usr/local/cuda-*/compat/lib; do
        if [[ -d $dir ]] && compgen -G "$dir/libcuda.so.*" > /dev/null; then
            COMPAT_DIR="$dir"
            break
        fi
    done

    # Datacenter GPU detection:
    # Match NVIDIA V100, T4, and A/H/L/B-series datacenter GPUs
    # (e.g., V100, T4, A2, A30, A40, A100, A800, H100, H200, L4, L40, B100).
    if [[ -n "$COMPAT_DIR" ]] &&
       [[ ! "$GPU_NAME" =~ (RTX|GeForce|Quadro|Titan) ]] &&
       [[ "$GPU_NAME" =~ (V100|T4|A[0-9]+|H[0-9]+|L[0-9]+|B[0-9]+) ]] &&
       awk "BEGIN {exit !($CC >= 7.0)}"; then

        # Ensure the compat lib symlink/file exists and can be resolved before using readlink
        if [[ -e "$COMPAT_DIR/libcuda.so.1" ]] && COMPAT_REALPATH=$(readlink -f "$COMPAT_DIR/libcuda.so.1"); then
            COMPAT_LIB=$(basename "$COMPAT_REALPATH")
            COMPAT_MAJOR=${COMPAT_LIB#libcuda.so.}
            COMPAT_MAJOR=${COMPAT_MAJOR%%.*}

            DRIVER_MAJOR=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)
            DRIVER_MAJOR=${DRIVER_MAJOR%%.*}

            if [[ -n "$COMPAT_MAJOR" && -n "$DRIVER_MAJOR" ]] &&
               (( DRIVER_MAJOR < COMPAT_MAJOR )); then
                echo "CUDA forward compatibility enabled (driver: $DRIVER_MAJOR, compat: $COMPAT_MAJOR)"
                echo "$COMPAT_DIR" > /etc/ld.so.conf.d/00-cuda-compat.conf
            fi
        fi
    fi
    ldconfig
fi


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
