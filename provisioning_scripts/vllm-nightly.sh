#!/bin/bash

set -euo pipefail

. /venv/main/bin/activate

# Replace the existing python version
conda install -y python=="${PYTHON_VERSION:-3.12}"

# Install Ray with dashboard

# Install nightly
uv pip install \
    blobfile vllm[flashinfer] ray[default] \
    --no-cache-dir \
    --pre \
    --extra-index-url https://wheels.vllm.ai/nightly \
    --index-strategy unsafe-best-match \
    --torch-backend auto

# Create vLLM startup script
cat > /opt/supervisor-scripts/vllm.sh << 'EOL'
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "vllm"

# Activate the venv
. /venv/main/bin/activate

# Check we are actually trying to serve a model
if [[ -z "${VLLM_MODEL:-}" ]]; then
    echo "Refusing to start ${PROC_NAME} (VLLM_MODEL not set)"
    sleep 6
    exit 0
fi

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Launch vllm
cd ${WORKSPACE}

# User has not specified a remote Ray server
if [[ -z "$RAY_ADDRESS" || "$RAY_ADDRESS" = "127.0.0.1"* ]]; then
    export RAY_ADDRESS="127.0.0.1:6379"

    # Wait until ps aux shows ray is running
    max_attempts=30
    attempt=1
    
    while true; do
        if ps aux | grep -v grep | grep -q "gcs_server"; then
            echo "Ray process detected - continuing"
            break
        fi
        
        if [ $attempt -ge $max_attempts ]; then
            echo "Timeout waiting for Ray process to start"
            exit 1
        fi
        
        echo "Waiting for Ray process to start (attempt $attempt/$max_attempts)..."
        sleep 2
        ((attempt++))
    done
fi

## Automatically use all GPUs
AUTO_PARALLEL_ARGS=""
# Rewrite var name
AUTO_PARALLEL=${AUTO_PARALLEL:-${USE_ALL_GPUS:-false}}
if [[ "${AUTO_PARALLEL,,}" = "true" ]] && ! [[ $VLLM_ARGS =~ tensor-parallel-size || $VLLM_ARGS =~ data-parallel-size ]]; then
    if [[ $VLLM_ARGS =~ enable-expert-parallel ]]; then
        AUTO_PARALLEL_ARGS="--tensor-parallel-size 1 --data-parallel-size $GPU_COUNT"
    else
        AUTO_PARALLEL_ARGS="--tensor-parallel-size $GPU_COUNT"
    fi
fi

vllm serve "${VLLM_MODEL:-}" ${VLLM_ARGS:---host 127.0.0.1 --port 18000} ${AUTO_PARALLEL_ARGS} 2>&1

EOL
chmod +x /opt/supervisor-scripts/vllm.sh

# Create Ray startup script

cat > /opt/supervisor-scripts/ray.sh << 'EOL'
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "ray dash"

# Activate the venv
. /venv/main/bin/activate

trap 'ray stop' EXIT

# Check we are actually trying to serve a model
if [[ -z "${VLLM_MODEL:-}" ]]; then
    echo "Refusing to start ${PROC_NAME} (VLLM_MODEL not set)"
    sleep 6
    exit 0
fi

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Launch Ray
cd ${WORKSPACE}

ray start ${RAY_ARGS:---head --port 6379  --dashboard-host 127.0.0.1 --dashboard-port 28265} 2>&1

sleep infinity
EOL
chmod +x /opt/supervisor-scripts/ray.sh

# Generate the supervisor config files
cat > /etc/supervisor/conf.d/vllm.conf << 'EOL'
[program:vllm]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/vllm.sh
autostart=true
autorestart=true
exitcodes=0
startsecs=0
stopasgroup=true
killasgroup=true
stopsignal=TERM
stopwaitsecs=10
# This is necessary for Vast logging to work alongside the Portal logs (Must output to /dev/stdout)
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_events_enabled=true
stdout_logfile_maxbytes=0
stdout_logfile_backups=0
EOL

cat > /etc/supervisor/conf.d/ray.conf << 'EOL'
[program:ray]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/ray.sh
autostart=true
autorestart=true
exitcodes=0
startsecs=0
stopasgroup=true
killasgroup=true
stopsignal=TERM
stopwaitsecs=10
# This is necessary for Vast logging to work alongside the Portal logs (Must output to /dev/stdout)
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_events_enabled=true
stdout_logfile_maxbytes=0
stdout_logfile_backups=0
EOL

# Update supervisor to start the new service
supervisorctl reread
supervisorctl update
