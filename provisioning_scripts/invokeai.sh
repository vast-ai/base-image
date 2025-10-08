#!/bin/bash

set -eou pipefail

. /venv/main/bin/activate

# Install InvokeAI
if [[ "${INVOKEAI_VERSION:-}" = "latest" ]]; then
    VERSION=""
else
    VERSION="==$INVOKEAI_VERSION"
fi
# Our pydantic version pinning patch may fail later - Catch that and install normally 
uv pip install invokeai${VERSION:-} pydantic==2.11.7 || uv pip install invokeai${VERSION:-}
# Patchmatch will likely fail to build if the python version is not 3.12 or 3.10, but it is non-fatal
apt install --no-install-recommends -y python3-opencv libopencv-dev
pip install pypatchmatch || true

# Create InvokeAI startup  scripts
cat > /opt/supervisor-scripts/invokeai.sh << 'EOL'
#!/bin/bash

# User can configure startup by removing the reference in /etc/portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

# Check for Invoke in the portal config
search_term="Invoke"
search_pattern=$(echo "$search_term" | sed 's/[ _-]/[ _-]/g')
if ! grep -qiE "^[^#].*${search_pattern}" /etc/portal.yaml; then
    echo "Skipping startup for ${PROC_NAME} (not in /etc/portal.yaml)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    exit 0
fi

echo "Starting InvokeAI." | tee "/var/log/portal/${PROC_NAME}.log"
. /venv/main/bin/activate
invokeai-web --root "${WORKSPACE}/invokeai" 2>&1 | tee -a "/var/log/portal/${PROC_NAME}.log"

EOL

chmod +x /opt/supervisor-scripts/invokeai.sh

# Generate the supervisor config file
cat > /etc/supervisor/conf.d/invokeai.conf << 'EOL'
[program:invokeai]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/invokeai.sh
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