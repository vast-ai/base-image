#!/bin/bash

mkdir -p "${WORKSPACE}"
cd "${WORKSPACE}"

# Forward compatibility for datacenter GPUs
if command -v nvidia-smi &> /dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)
    CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n1)

    # Exclude known non-datacenter branding (consumer / workstation)
    if [[ ! "$GPU_NAME" =~ (RTX|GeForce|Quadro|Titan) ]]; then

        # Forward compatibility requires Volta+ (SM >= 7.0)
        if awk "BEGIN {exit !($CC >= 7.0)}"; then

            # Datacenter GPU families eligible for CUDA Forward Compatibility
            if [[ "$GPU_NAME" =~ (V100|T4|A[0-9]{1,3}|H[0-9]{2,3}|L[0-9]{1,2}|B[0-9]{2,3}) ]]; then
                echo "CUDA forward-compatible datacenter GPU detected"
                export LD_LIBRARY_PATH=/usr/local/cuda/compat${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}
            fi
        fi
    fi
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
