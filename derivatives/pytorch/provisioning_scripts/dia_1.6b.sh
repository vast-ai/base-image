#!/bin/bash 
set -eou pipefail

. /venv/main/bin/activate

cd ${DATA_DIRECTORY}

git clone https://github.com/nari-labs/dia.git

cd dia

pip install -e .

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
python app.py 2>&1 | tee -a "/var/log/portal/${PROC_NAME}.log"

EOL

chmod +x /opt/supervisor-scripts/dia.sh

cat > /etc/supervisor/conf.d/dia.conf << 'EOL'
[program:dia]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/dia.sh
autostart=true
autorestart=unexpected
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

supervisorctl reread
supervisorctl update
