#!/bin/bash

# Provisioning should not complete if there is an error. Retry on reboot if it failed
set -euo pipefail

# Ensure downloaded models are in the workspace
export HF_HOME="${DATA_DIRECTORY}/huggingface"

# Allow user to fetch alternative repo, branch, tag, commit
export APP_REPO_URL=${APP_REPO_URL:-https://github.com/nari-labs/dia}
export APP_REF=${APP_REF:-main}
export MODEL=${MODEL:-nari-labs/Dia-1.6B}

# Install the software into the default venv
. /venv/main/bin/activate
cd "${DATA_DIRECTORY}"
git clone "$APP_REPO_URL"
cd $(basename "$APP_REPO_URL")
git checkout "$APP_REF"
pip install -e .

# Download the models.  Not strictly necessary because app.py handles this but gives user more insight into the startup and allows retry on failure
MAX_ATTEMPTS=5
DELAY_SECONDS=10
ATTEMPT=1
echo "Downloading $MODEL from HuggingFace..."
while [ $ATTEMPT -le $MAX_ATTEMPTS ]
do
    echo "Attempt $ATTEMPT of $MAX_ATTEMPTS..."
    
    huggingface-cli download "$MODEL" --include "*.json" "*.safetensors" 2>&1
    
    if [ $? -eq 0 ]; then
        echo "Download completed successfully!"
        break
    else
        echo "Attempt $ATTEMPT failed. Waiting $DELAY_SECONDS seconds before retrying..."
        sleep $DELAY_SECONDS
        ATTEMPT=$((ATTEMPT+1))
    fi
done

if [ $ATTEMPT -gt $MAX_ATTEMPTS ]; then
    echo "WARNING: Maximum attempts reached. Download of $MODEL failed.  You will need to manually download the model."
fi


# Generate the launch script for supervisord
cat > /opt/supervisor-scripts/dia.sh << 'EOL'
#!/bin/bash

# User can configure startup by removing the reference in /etc/portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

# Check for Dia in the portal config
search_term="Dia"
search_pattern=$(echo "$search_term" | sed 's/[ _-]/[ _-]/g')
if ! grep -qiE "^[^#].*${search_pattern}" /etc/portal.yaml; then
    echo "Skipping startup for ${PROC_NAME} (not in /etc/portal.yaml)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    exit 0
fi

# Activate the venv
. /venv/main/bin/activate

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 10
done

cd ${DATA_DIRECTORY}/dia
export HF_HOME="${DATA_DIRECTORY}/huggingface"
export GRADIO_SERVER_NAME=${GRADIO_SERVER_NAME:-127.0.0.1}
export GRADIO_SERVER_PORT=${GRADIO_SERVER_PORT:-17860}

echo "Starting Nari-Labs Dia application.  UI will be available when the models have been fetched and loaded." | tee -a "/var/log/portal/${PROC_NAME}.log"

python app.py 2>&1 | tee -a "/var/log/portal/${PROC_NAME}.log"

EOL

chmod +x /opt/supervisor-scripts/dia.sh

# Generate the supervisor config file
cat > /etc/supervisor/conf.d/dia.conf << 'EOL'
[program:dia]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/dia.sh
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
