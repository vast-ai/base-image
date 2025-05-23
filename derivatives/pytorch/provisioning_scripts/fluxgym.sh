#!/bin/bash

# Provisioning should not complete if there is an error. Retry on reboot if it failed
set -eou pipefail

# Allow user to fetch alternative repo, branch, tag, commit
export APP_REPO_URL=${APP_REPO_URL:-https://github.com/cocktailpeanut/fluxgym}
export APP_REF=${APP_REF:-main}

# Install the software into the default venv
. /venv/main/bin/activate
cd "${DATA_DIRECTORY}"

git clone "$APP_REPO_URL"
cd $(basename "$APP_REPO_URL")
git checkout "$APP_REF"

# Kohya Scripts requirements
git clone -b sd3 https://github.com/kohya-ss/sd-scripts
cd sd-scripts
pip install -r requirements.txt

# Flux Gym requirements
cd ..
pip install -r requirements.txt
pip install -U bitsandbytes


# Generate the launch script for supervisord
cat > /opt/supervisor-scripts/fluxgym.sh << 'EOL'
#!/bin/bash

# User can configure startup by removing the reference in /etc/portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

# Check for Flux in the portal config
search_term="Flux"
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

cd ${DATA_DIRECTORY}/fluxgym

GRADIO_SERVER_PORT=17860 python app.py 2>&1 | tee -a "/var/log/portal/${PROC_NAME}.log"

EOL

chmod +x /opt/supervisor-scripts/fluxgym.sh

# Generate the supervisor config file
cat > /etc/supervisor/conf.d/fluxgym.conf << 'EOL'
[program:fluxgym]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/fluxgym.sh
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