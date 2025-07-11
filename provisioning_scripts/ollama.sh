#!/bin/bash

set -eou pipefail

# Install Ollama if not present
which ollama > /dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | sh

# Create Ollama startup  scripts
cat > /opt/supervisor-scripts/ollama.sh << 'EOL'
#!/bin/bash

# User can configure startup by removing the reference in /etc/portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

# Check for Ollama in the portal config
search_term="Ollama"
search_pattern=$(echo "$search_term" | sed 's/[ _-]/[ _-]/g')
if ! grep -qiE "^[^#].*${search_pattern}" /etc/portal.yaml; then
    echo "Skipping startup for ${PROC_NAME} (not in /etc/portal.yaml)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    exit 0
fi

echo "Starting Ollama." | tee "/var/log/portal/${PROC_NAME}.log"

ollama serve 2>&1 | tee -a "/var/log/portal/${PROC_NAME}.log"
EOL

chmod +x /opt/supervisor-scripts/ollama.sh

# Generate the supervisor config file
cat > /etc/supervisor/conf.d/ollama.conf << 'EOL'
[program:ollama]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/ollama.sh
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

echo "Waiting for Ollama server to start..."
while ! curl -s localhost:11434 > /dev/null 2>&1; do
    echo "Server not ready yet, waiting 2 seconds..."
    sleep 2
done

# Use run because we can watch for load failures in the ollama serve log
ollama run ${OLLAMA_ARGS:-} ${OLLAMA_MODEL:-qwen3:8b} > /dev/null 2>&1 &

FAIL_STRINGS=(
    "Error: pull model manifest:"
)

# Build grep pattern for fail strings
fail_pattern=$(IFS='|'; echo "${FAIL_STRINGS[*]}")

set +o pipefail
tail -f /var/log/portal/ollama.log 2>/dev/null | while read -r line; do
    # Check for success condition
    if [[ "$line" == *"llama runner started"* ]]; then
        exit 0
    fi
    
    # Check for failure conditions using grep
    if echo "$line" | grep -qE "$fail_pattern"; then
        exit 1
    fi
done
pipeline_exit=$?
set -o pipefail

exit $pipeline_exit
