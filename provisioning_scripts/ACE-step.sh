#!/bin/bash

set -euo pipefail

. /venv/main/bin/activate

cd "$WORKSPACE"
[[ -d "${WORKSPACE}/ACE-Step" ]] || git clone https://github.com/ace-step/ACE-Step
cd ACE-Step
[[ -n "${ACE_STEP_VERSION:-}" ]] && git checkout "$ACE_STEP_VERSION"

# torchaudio >= 2.9 and torchcodec do not pin torch, so an unpinned companion
# resolves to the newest release and breaks against an older torch. Pin them
# from derivatives/pytorch/torch-companions.json (copied here: this script runs
# standalone). ACE-Step needs torchaudio, which ends at 2.11.0.
if [[ -n "${TORCH_VERSION:-}" ]]; then
    case "$TORCH_VERSION" in
        2.7.1)  companions="torchvision==0.22.1 torchaudio==2.7.1 torchcodec==0.5" ;;
        2.8.0)  companions="torchvision==0.23.0 torchaudio==2.8.0 torchcodec==0.7.0" ;;
        2.9.1)  companions="torchvision==0.24.1 torchaudio==2.9.1 torchcodec==0.9.1" ;;
        2.10.0) companions="torchvision==0.25.0 torchaudio==2.10.0 torchcodec==0.10.0" ;;
        2.11.0) companions="torchvision==0.26.0 torchaudio==2.11.0 torchcodec==0.11.0" ;;
        *) echo "TORCH_VERSION=${TORCH_VERSION} unsupported; use 2.7.1, 2.8.0, 2.9.1, 2.10.0 or 2.11.0, or leave unset for latest" >&2; exit 1 ;;
    esac
    uv pip install torch=="$TORCH_VERSION" $companions --torch-backend "${TORCH_BACKEND:-cu128}"
else
    uv pip install torch torchaudio torchvision torchcodec --torch-backend "${TORCH_BACKEND:-cu128}"
fi
uv pip install -r requirements.txt gradio'<6' peft'<0.18' --torch-backend "${TORCH_BACKEND:-cu128}"
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
