#!/bin/bash
set -euo pipefail

. /venv/main/bin/activate

OLLAMA_SCRIPT="https://raw.githubusercontent.com/vast-ai/base-image/refs/heads/main/provisioning_scripts/ollama.sh"
(curl -fsSL "$OLLAMA_SCRIPT" | bash) &

which langflow > /dev/null 2>&1 || uv pip install langflow${LANGFLOW_VERSION:+==$LANGFLOW_VERSION}

# Create Langflow startup scripts
cat > /opt/supervisor-scripts/langflow.sh << 'EOL'
#!/bin/bash

. /venv/main/bin/activate

# User can configure startup by removing the reference in /etc/portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

# Check for Langflow in the portal config
search_term="Langflow"
search_pattern=$(echo "$search_term" | sed 's/[ _-]/[ _-]/g')
if ! grep -qiE "^[^#].*${search_pattern}" /etc/portal.yaml; then
    echo "Skipping startup for ${PROC_NAME} (not in /etc/portal.yaml)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    exit 0
fi

echo "Starting Langflow." | tee "/var/log/portal/${PROC_NAME}.log"

langflow run ${LANGFLOW_ARGS:-} 2>&1 | tee -a "/var/log/portal/${PROC_NAME}.log"
EOL

chmod +x /opt/supervisor-scripts/langflow.sh

# Generate the supervisor config file
cat > /etc/supervisor/conf.d/langflow.conf << 'EOL'
[program:langflow]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/langflow.sh
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

wait -n