#!/bin/bash

main() {
    local propagate_user_keys=true
    local export_env=true
    local generate_tls_cert=true

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --no-user-keys)
                propagate_user_keys=false
                shift
                ;;
            --no-export-env)
                export_env=false
                shift
                ;;
            --no-cert-gen)
                generate_tls_cert=false
                shift
                ;;
            *)
                echo "Warning: Unknown flag: $1" >&2
                shift
                ;;
        esac
    done

    # Remove Jupyter from the portal config if the external port 8080 isn't defined
    if [ -z "${VAST_TCP_PORT_8080}" ]; then
        PORTAL_CONFIG=$(echo "$PORTAL_CONFIG" | tr '|' '\n' | grep -vi jupyter | tr '\n' '|' | sed 's/|$//')
    fi

    # Ensure correct port mappings for Jupyter when running in Jupyter launch mode
    if [[ -f /.launch ]] && grep -qi jupyter /.launch; then
        PORTAL_CONFIG="$(echo "$PORTAL_CONFIG" | sed 's#localhost:8080:18080#localhost:8080:8080#g')"
    fi

    # First run...
    if [[ ! -f /.first_boot_complete ]]; then
        echo "Applying first boot optimizations..."
        # Prevent dubious ownership 
        set_git_safe_dirs
        # Ensure we have an up-to-date version of the CLI tool
        (cd /opt/vast-cli && git pull)
        # Attempt to upgrade Instance Portal to latest or specified version
        update-portal ${PORTAL_VERSION:+-v $PORTAL_VERSION}
        # Copy /opt/workspace-internal to /workspace - Brings to top layer and will support mounting a volume
        # Ensure user 1001 has full access - Avoids permission errors if running with the normal user
        workspace=${WORKSPACE:-/workspace}
        mkdir -p "${workspace}/" > /dev/null 2>&1
        chown -f 1001:1001 "${workspace}/" > /dev/null 2>&1
        chmod g+s "${workspace}/" > /dev/null 2>&1
        find "${workspace}/" -type d -exec chmod g+s {} + > /dev/null 2>&1
        chmod 775 "${workspace}/" > /dev/null 2>&1
        sudo -u user cp -rn /opt/workspace-internal/. "${workspace}/"
        find "${workspace}/" -type d -exec chmod g+s {} + > /dev/null 2>&1
        find "${workspace}/" -not -user 1001 -exec chown 1001:1001 {} +
        setfacl -R -d -m g:user:rw- "${workspace}/" > /dev/null 2>&1
        # Let the 'user' account connect via SSH
        [[ "${propagate_user_keys}" = "true" ]] && /opt/instance-tools/bin/propagate_ssh_keys.sh
        # Initial venv backup - Also runs as a cron job every 30 minutes
        /opt/instance-tools/bin/venv-backup.sh
        # Populate /etc/environment - Skip HOME directory and ensure values are enclosed in double quotes
        env -0 | grep -zv "^HOME=" | while IFS= read -r -d '' line; do
            name=${line%%=*}
            value=${line#*=}
            printf '%s="%s"\n' "$name" "$value"
        done > /etc/environment
        # Ensure /etc/environment is sourced on login
        echo '. /opt/instance-tools/bin/export_env.sh' | tee -a /root/.bashrc /home/user/.bashrc
        # Ensure users are dropped into the venv on login.  Must be after /.launch has updated PS1 
        echo 'cd ${WORKSPACE} && if [ -f "${WORKSPACE}/venv/${ACTIVE_VENV:-main}/bin/activate" ]; then source "${WORKSPACE}/venv/${ACTIVE_VENV:-main}/bin/activate"; else source /venv/${ACTIVE_VENV:-main}/bin/activate; fi' | tee -a /root/.bashrc /home/user/.bashrc
        # Warn CLI users if the container provisioning is not yet complete. Red >>>
        echo '[[ -f /.provisioning ]] && echo -e "\e[91m>>>\e[0m Instance provisioning is not yet complete.\n\e[91m>>>\e[0m Required software may not be ready.\n\e[91m>>>\e[0m See /var/log/portal/provisioning.log or the Instance Portal web app for progress updates\n\n"' | tee -a /root/.bashrc /home/user/.bashrc
        touch /.first_boot_complete
    fi

    # Source the file at /etc/environment - We can now edit environment variables in a running instance
    [[ "${export_env}" = "true" ]] && . /opt/instance-tools/bin/export_env.sh

    # We may be busy for a while.
    # Indicator for supervisor scripts to prevent launch during provisioning if necessary (if [[ -f /.provisioning ]] ...)
    touch /.provisioning

    # Generate the Jupyter certificate if run in SSH/Args Jupyter mode
    sleep 2
    if [[ "${generate_tls_cert}" = "true" ]] && [[ ! -f /etc/instance.key && ! -f /etc/instance.crt ]]; then
        if [ ! -f /etc/openssl-san.cnf ] || ! grep -qi vast /etc/openssl-san.cnf; then
            echo "Generating certificates"
            echo '[req]' > /etc/openssl-san.cnf;
            echo 'default_bits       = 2048' >> /etc/openssl-san.cnf;
            echo 'distinguished_name = req_distinguished_name' >> /etc/openssl-san.cnf;
            echo 'req_extensions     = v3_req' >> /etc/openssl-san.cnf;

            echo '[req_distinguished_name]' >> /etc/openssl-san.cnf;
            echo 'countryName         = US' >> /etc/openssl-san.cnf;
            echo 'stateOrProvinceName = CA' >> /etc/openssl-san.cnf;
            echo 'organizationName    = Vast.ai Inc.' >> /etc/openssl-san.cnf;
            echo 'commonName          = vast.ai' >> /etc/openssl-san.cnf;

            echo '[v3_req]' >> /etc/openssl-san.cnf;
            echo 'basicConstraints = CA:FALSE' >> /etc/openssl-san.cnf;
            echo 'keyUsage         = nonRepudiation, digitalSignature, keyEncipherment' >> /etc/openssl-san.cnf;
            echo 'subjectAltName   = @alt_names' >> /etc/openssl-san.cnf;

            echo '[alt_names]' >> /etc/openssl-san.cnf;
            echo 'IP.1   = 0.0.0.0' >> /etc/openssl-san.cnf;

            openssl req -newkey rsa:2048 -subj "/C=US/ST=CA/CN=jupyter.vast.ai/" -nodes -sha256 -keyout /etc/instance.key -out /etc/instance.csr -config /etc/openssl-san.cnf
            curl --header 'Content-Type: application/octet-stream' --data-binary @//etc/instance.csr -X POST "https://console.vast.ai/api/v0/sign_cert/?instance_id=${CONTAINER_ID:-${VAST_CONTAINERLABEL#C.}}" > /etc/instance.crt;
        fi
    fi

    # If there is no key present we should ensure supervisor is aware
    if [[ ! -f /etc/instance.key || ! -f /etc/instance.crt ]]; then
        export ENABLE_HTTPS=false
    fi

    # Now we run supervisord - Put it in the background so provisioning can be monitored in Instance Portal
    supervisord \
        -n \
        -u root \
        -c /etc/supervisor/supervisord.conf &
    supervisord_pid=$!

    # Provision the instance with a remote script - This will run on every startup until it has successfully completed without errors
    # This is for configuration of existing images and will also allow for templates to be created without building docker images
    # Experienced users will be able to convert the script to Dockerfile RUN and build a self-contained image
    # NOTICE: If the provisioning script introduces new supervisor processes it must:
    # - Remove the file /etc/portal.yaml
    # - run `supervisorctl reload`

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

    # Remove the blocker and leave supervisord to run
    rm -f /.provisioning
    wait $supervisord_pid
}

set_git_safe_dirs() {
    # Prevents dubious ownership issues for root user.
    for dir in /opt/workspace-internal/*; do
        # Check if it's a directory
        if [ -d "$dir" ]; then
            # Get just the directory name without the path
            dirname=$(basename "$dir")
            
            # Set the target path
            target_path="${WORKSPACE:-/workspace}/${dirname}"

            # Handle parent directory
            echo "Adding safe.directory: $target_path"
            git config --global --add safe.directory "$target_path"
            
            # Handle submodules
            find $target_path -name ".git" | while read gitpath; do
                parent_dir=$(dirname "$gitpath")
                git config --global --add safe.directory "$parent_dir"
            done
        fi
    done
}

main "$@"