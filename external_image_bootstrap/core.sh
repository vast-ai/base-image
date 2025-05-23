#!/bin/bash

DEBIAN_FRONTEND=noninteractive
case $(uname -m) in
    x86_64) export CPU_ARCH="amd64" ;;
    aarch64) export CPU_ARCH="arm64" ;;
    *) uname -m ;;
esac

main(){
    if [[ ! -f /.first_boot_complete ]]; then
        env -0 | grep -zv "^HOME=" | while IFS= read -r -d '' line; do
            name=${line%%=*}
            value=${line#*=}
            printf '%s="%s"\n' "$name" "$value"
        done > /etc/environment

        # Instance startup handles the first apt update
        apt-get install --no-install-recommends -y \
            software-properties-common \
            gpg-agent

        add-apt-repository -y ppa:deadsnakes/ppa
        add-apt-repository -y ppa:deadsnakes/nightly
        
        mkdir -p /etc/apt/preferences.d && \
        echo $'Package: *\nPin: release o=LP-PPA-deadsnakes-ppa\nPin-Priority: 900\n\nPackage: *\nPin: release o=LP-PPA-deadsnakes-nightly\nPin-Priority: 50' \
            > /etc/apt/preferences.d/deadsnakes-priority
        
        # Get some essentials
        apt-get update
        apt-get install --no-install-recommends -y \
            wget \
            curl \
            jq \
            nano \
            vim \
            ca-certificates \
            python3.10-venv \
            supervisor

        # Install Instance Portal
        [[ -z "$PORTAL_REPO" ]] && export PORTAL_REPO="vast-ai/base-image"
        [[ -z "$PORTAL_VERSION" ]] && export PORTAL_VERSION=$(curl -s https://api.github.com/repos/${PORTAL_REPO}/releases/latest | jq -r .tag_name)
        PORTAL_DOWNLOAD_URL="https://github.com/$PORTAL_REPO/releases/download/$PORTAL_VERSION/instance-portal.tar.gz"
        curl -L "$PORTAL_DOWNLOAD_URL" -o /tmp/instance-portal.tar.gz && \
        tar -xzf /tmp/instance-portal.tar.gz -C /opt
        python3.10 -m venv /opt/portal-aio/venv
        /opt/portal-aio/venv/bin/pip install -r /opt/portal-aio/requirements.txt
        ln -s $(which caddy) /opt/portal-aio/caddy_manager/caddy
        wget -O /opt/portal-aio/tunnel_manager/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CPU_ARCH}
        chmod +x /opt/portal-aio/tunnel_manager/cloudflared
        # TODO: Build and host appropriate aarch64 caddy version
        curl -o /opt/portal-aio/caddy_manager/caddy.gz https://vast2.s3.amazonaws.com/caddyserver/v2.8.4/caddy.gz
        gunzip /opt/portal-aio/caddy_manager/caddy.gz 
        chmod +x /opt/portal-aio/caddy_manager/caddy
        
        # Set up important directories
        mkidir -p /workspace
        mkdir -p /var/log/portal
        mkdir -p /opt/supervisor-scripts

        # Write default process config and launcher scripts
        write_default_supervisor_configs
        write_default_supervisor_scripts

        # Mark first boot process complete 
        touch /.first_boot_complete
    fi

    # Ensure the user can edit environment variables easily
    set -a 
    . /etc/environment
    set +a

    # We should have keys and certs, but if we do not we cannot run HTTPS mode
    if [[ ! -f /etc/instance.key || ! -f /etc/instance.crt ]]; then
        export ENABLE_HTTPS=false
    fi

    # This will launch our default processes (Caddy, Portal, Tunnel Manager).  
    # Scripts sourcing this file can reread and update to launch additional processes
    supervisord \
            -n \
            -u root \
            -c /etc/supervisor/supervisord.conf &
        supervisord_pid=$!

    # Provision the instance with a remote script - This will run on every startup until it has successfully completed without errors
    # This is for configuration of existing images and will also allow for templates to be created without building docker images
    # NOTICE: If the provisioning script introduces new supervisor processes it must:
    # - run `supervisorctl reread && supervisorctl update`
    if [[ -n $PROVISIONING_SCRIPT && ! -f /.provisioning_complete ]]; then
        echo "*****"
        echo "*"
        echo "*"
        echo "* Provisioning instance with remote script from ${PROVISIONING_SCRIPT}"
        echo "*"
        echo "* This may take a while.  Some services may not start until this process completes."
        echo "* To change this behavior you can edit or remove the PROVISIONING_SCRIPT environment variable."
        echo "*"
        echo "*"
        echo "*****"
        # Only download it if we don't already have it - Allows inplace modification & restart
        [[ ! -f /provisioning.sh ]] && curl -Lo /provisioning.sh "$PROVISIONING_SCRIPT"
        chmod +x /provisioning.sh && \
        (set -o pipefail; /provisioning.sh 2>&1 | tee -a /var/log/portal/provisioning.log) && \
        touch /.provisioning_complete && \
        echo "Provisioning complete!" | tee -a /var/log/portal/provisioning.log

        [[ ! -f /.provisioning_complete ]] && echo "Note: Provisioning encountered issues but instance startup will continue" | tee -a /var/log/portal/provisioning.log
    fi
}

write_default_supervisor_configs() {
cat > /etc/supervisor/conf.d/caddy.conf << 'CADDY'
[program:caddy]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/caddy.sh
autostart=true
autorestart=true
exitcodes=0
startsecs=0
stopasgroup=true
killasgroup=true
stopsignal=TERM
stopwaitsecs=10
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_events_enabled=true
stdout_logfile_maxbytes=0
stdout_logfile_backups=0
CADDY

cat > /etc/supervisor/conf.d/instance_portal.conf << 'INSTANCE_PORTAL'
[program:instance_portal]
environment=PROC_NAME="%(program_name)s".
TUNNEL_MANAGER="http://localhost:11112"
command=/opt/supervisor-scripts/instance_portal.sh
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
INSTANCE_PORTAL

cat > /etc/supervisor/conf.d/tunnel_manager.conf << 'TUNNEL_MANAGER'
[program:tunnel_manager]
environment=PROC_NAME="%(program_name)s",
CLOUDFLARE_METRICS="localhost:11113"
command=/opt/supervisor-scripts/tunnel_manager.sh
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
TUNNEL_MANAGER
}

write_default_supervisor_scripts() {
# Write Caddy startup script to file
cat > /opt/supervisor-scripts/caddy.sh << 'CADDY'
#!/bin/bash

# Run the caddy configurator
cd /opt/portal-aio/caddy_manager
/opt/portal-aio/venv/bin/python caddy_config_manager.py | tee -a "/var/log/portal/${PROC_NAME}.log"

# Ensure the portal config file exists if running without PORTAL_CONFIG
touch /etc/portal.yaml

if [[ -f /etc/Caddyfile ]]; then
    # Frontend log viewer will force a page reload if this string is detected
    echo "Starting Caddy..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    /opt/portal-aio/caddy_manager/caddy run --config /etc/Caddyfile 2>&1 | tee -a "/var/log/portal/${PROC_NAME}.log"
else
    echo "Not Starting Caddy - No config file was generated" | tee -a "/var/log/portal/${PROC_NAME}.log"
fi
CADDY
chmod +x /opt/supervisor-scripts/caddy.sh

# Write Instance Portal startup script to file
cat > /opt/supervisor-scripts/instance_portal.sh << 'INSTANCE_PORTAL'
#!/bin/bash

# User can configure startup by removing the reference in /etc.portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

# Check for $search_term in the portal config
search_term="instance portal"
search_pattern=$(echo "$search_term" | sed 's/[ _-]/[ _-]?/gi')
if ! grep -qiE "^[^#].*${search_pattern}" /etc/portal.yaml; then
    echo "Skipping startup for ${PROC_NAME} (not in /etc/portal.yaml)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    exit 0
fi

cd /opt/portal-aio/portal
# Log outside of /var/log/portal
/opt/portal-aio/venv/bin/fastapi run --host 127.0.0.1 --port 11111 portal.py 2>&1 | tee "/var/log/${PROC_NAME}.log"
INSTANCE_PORTAL
chmod +x /opt/supervisor-scripts/instance_portal.sh

# Write Tunnel Manager startup script to file
cat > /opt/supervisor-scripts/tunnel_manager.sh << 'TUNNEL_MANAGER'
#!/bin/bash

# User can configure startup by removing the reference in /etc.portal.yaml - So wait for that file and check it
while [ ! -f "$(realpath -q /etc/portal.yaml 2>/dev/null)" ]; do
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..." | tee -a "/var/log/portal/${PROC_NAME}.log"
    sleep 1
done

# Check for $search_term in the portal config
search_term="instance portal"
search_pattern=$(echo "$search_term" | sed 's/[ _-]/[ _-]?/gi')
if ! grep -qiE "^[^#].*${search_pattern}" /etc/portal.yaml; then
    echo "Skipping startup for ${PROC_NAME} (not in /etc/portal.yaml)" | tee -a "/var/log/portal/${PROC_NAME}.log"
    exit 0
fi

cd /opt/portal-aio/tunnel_manager
# Log outside of /var/log/portal
/opt/portal-aio/venv/bin/fastapi run --host 127.0.0.1 --port 11112 tunnel_manager.py 2>&1 | tee "/var/log/${PROC_NAME}.log"

TUNNEL_MANAGER
chmod +x /opt/supervisor-scripts/tunnel_manager.sh
}

main "$@"