#!/bin/bash
set -euo pipefail

. /venv/main/bin/activate

cd "${WORKSPACE}"

[[ ! -d ACE-Step-1.5 ]] && git clone https://github.com/ace-step/ACE-Step-1.5
[[ ! -d ace-step-ui ]] && git clone https://github.com/fspecii/ace-step-ui

cd "${WORKSPACE}/ACE-Step-1.5"
git checkout "${ACE_STEP_REF:-main}"
UV_PROJECT_ENVIRONMENT=/venv/main uv sync

cd "${WORKSPACE}/ace-step-ui"
git checkout "${ACE_STEP_UI_REF:-main}"
. /opt/nvm/nvm.sh
npm install
cd server
npm install
[[ ! -f .env ]] && cp .env.example .env


# Create startup script
cat > /opt/supervisor-scripts/ace-step-api.sh << 'EOL'
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "ACE Step API"

. /venv/main/bin/activate

echo "Starting ACE Step API"

cd "${WORKSPACE}/ACE-Step-1.5"
UV_PROJECT_ENVIRONMENT=/venv/main/ uv run acestep-api --port 8001
EOL

chmod +x /opt/supervisor-scripts/ace-step-api.sh

cat > /opt/supervisor-scripts/ace-step-ui.sh << 'EOL'
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "ACE Step UI"

export ACESTEP_LM_MODEL_PATH=${ACESTEP_LM_MODEL_PATH:=acestep-5Hz-lm-4B}

until curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/docs | grep -q 200; do
  echo "Waiting for ACE Step API..."
  sleep 5
done
echo "Service is up!"

echo "Starting ACE Step UI"

cd "${WORKSPACE}/ace-step-ui"
. /opt/nvm/nvm.sh
ACESTEP_PATH="${WORKSPACE:-/workspace}/ACE-Step-1.5/" ./start.sh
EOL

chmod +x /opt/supervisor-scripts/ace-step-ui.sh

# Generate the supervisor config files
cat > /etc/supervisor/conf.d/ace-step-api.conf << 'EOL'
[program:ace-step-api]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/ace-step-api.sh
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

cat > /etc/supervisor/conf.d/ace-step-ui.conf << 'EOL'
[program:ace-step-ui]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/ace-step-ui.sh
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