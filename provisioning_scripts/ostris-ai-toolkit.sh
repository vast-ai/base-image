#!/bin/bash

set -eou pipefail

. /venv/main/bin/activate

cd "$WORKSPACE"
[[ -d "${WORKSPACE}/ai-toolkit" ]] || git clone https://github.com/ostris/ai-toolkit
cd ai-toolkit
[[ -n "${AI_TOOLKIT_VERSION:-}" ]] && git checkout "$AI_TOOLKIT_VERSION"

uv pip install torch==${TORCH_VERSION:-2.7.0} torchvision torchaudio --torch-backend=auto
uv pip install -r requirements.txt

# Create AI Toolkit startup script
cat > /opt/supervisor-scripts/ai-toolkit.sh << 'EOL'
#!/bin/bash

kill_subprocesses() {
    local pid=$1
    local subprocesses=$(pgrep -P "$pid")
    
    for process in $subprocesses; do
        kill_subprocesses "$process"
    done
    
    if [[ -n "$subprocesses" ]]; then
        kill -TERM $subprocesses 2>/dev/null
    fi
}

cleanup() {
    kill_subprocesses $$
    sleep 2
    pkill -KILL -P $$ 2>/dev/null
    exit 0
}

trap cleanup EXIT INT TERM

# User can configure startup by removing the reference in /etc/portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

# Check for Wan Text in the portal config
search_term="AI Toolkit"
search_pattern=$(echo "$search_term" | sed 's/[ _-]/[ _-]/g')
if ! grep -qiE "^[^#].*${search_pattern}" /etc/portal.yaml; then
    echo "Skipping startup for ${PROC_NAME} (not in /etc/portal.yaml)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    exit 0
fi

echo "Starting AI Toolkit" | tee "/var/log/portal/${PROC_NAME}.log"
. /venv/main/bin/activate
. /opt/nvm/nvm.sh

cd "${WORKSPACE}/ai-toolkit/ui"
${AI_TOOLKIT_START_CMD:-npm run build_and_start} 2>&1 | tee "/var/log/portal/${PROC_NAME}.log"

EOL

chmod +x /opt/supervisor-scripts/ai-toolkit.sh

# Generate the supervisor config files
cat > /etc/supervisor/conf.d/ai-toolkit.conf << 'EOL'
[program:ai-toolkit]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/ai-toolkit.sh
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