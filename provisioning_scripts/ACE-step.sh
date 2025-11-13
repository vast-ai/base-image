#!/bin/bash

set -euo pipefail

. /venv/main/bin/activate

cd "$WORKSPACE"
[[ -d "${WORKSPACE}/ACE-Step" ]] || git clone https://github.com/ace-step/ACE-Step
cd ACE-Step
[[ -n "${ACE_STEP_VERSION:-}" ]] && git checkout "$ACE_STEP_VERSION"

uv pip install torch"${TORCH_VERSION:+==$TORCH_VERSION}" torchaudio torchvision torchcodec
uv pip install -r requirements.txt peft'<0.18' --torch-backend auto
uv pip install -e .

# Create ACE Step startup script
cat > /opt/supervisor-scripts/ace-step.sh << 'EOL'
#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"
. "${utils}/exit_portal.sh" "ace step"

echo "Starting Ace Step"
. /venv/main/bin/activate

cd "${WORKSPACE}/ACE-Step"
acestep ${ACE_STEP_ARGS:---port 7865 --torch_compile true --bf16 true} 2>&1

EOL

chmod +x /opt/supervisor-scripts/ace-step.sh

# Generate the supervisor config files
cat > /etc/supervisor/conf.d/ace-step.conf << 'EOL'
[program:ace-step]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/ace-step.sh
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
