#!/bin/bash

# Provisioning should not complete if there is an error. Retry on reboot if it failed
set -euo pipefail

# Ensure downloaded models are in the workspace
HF_HOME="${HF_HOME:-${DATA_DIRECTORY}/huggingface}"

# Allow user to fetch alternative repo, branch, tag, commit
APP_REPO_URL=${APP_REPO_URL:-https://github.com/jhj0517/Whisper-WebUI}
APP_DIR="${APP_DIR:-${WORKSPACE}/$(basename $APP_REPO_URL)}"
APP_REF=${APP_REF:-master}
TORCH_VERSION=${TORCH_VERSION:-2.8.0}

# Install the software into the default venv
. /venv/main/bin/activate

[[ -d "$APP_DIR" ]] || git clone "$APP_REPO_URL" "$APP_DIR"

cd "$APP_DIR"
git checkout "$APP_REF"

uv pip install torch=="${TORCH_VERSION}" torchaudio --torch-backend auto

uv pip install -r requirements.txt -r backend/requirements-backend.txt

# Generate the launch script for supervisord
cat > /opt/supervisor-scripts/whisper-webui.sh << 'EOL'
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Whisper WebUI"

# Activate the venv
. /venv/main/bin/activate

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

APP_REPO_URL=${APP_REPO_URL:-https://github.com/jhj0517/Whisper-WebUI}
APP_DIR="${APP_DIR:-${WORKSPACE}/$(basename $APP_REPO_URL)}"

cd "$APP_DIR"

echo "Starting Whisper WebUI application."

python app.py ${WHISPER_UI_ARGS:---whisper_type whisper --server_port 7860}
EOL

chmod +x /opt/supervisor-scripts/whisper-webui.sh

cat > /opt/supervisor-scripts/whisper-api.sh << 'EOL'
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "Whisper API"

# Activate the venv
. /venv/main/bin/activate

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

APP_REPO_URL=${APP_REPO_URL:-https://github.com/jhj0517/Whisper-WebUI}
APP_DIR="${APP_DIR:-${WORKSPACE}/$(basename $APP_REPO_URL)}"

cd "$APP_DIR"

echo "Starting Whisper API application."

uvicorn backend.main:app ${WHISPER_API_ARGS:---port 8000}
EOL

chmod +x /opt/supervisor-scripts/whisper-api.sh

# Generate the supervisor config file
cat > /etc/supervisor/conf.d/whisper-webui.conf << 'EOL'
[program:whisper-webui]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/whisper-webui.sh
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

cat > /etc/supervisor/conf.d/whisper-api.conf << 'EOL'
[program:whisper-api]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/whisper-api.sh
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
