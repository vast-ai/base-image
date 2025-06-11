#!/bin/bash

main() {
    local propagate_user_keys=true
    local export_env=true
    local generate_tls_cert=true
    local activate_python_environment=true
    # Default behavior is sync only if $WORKSPACE is a volume
    local sync_python_environment=$(mountpoint "$WORKSPACE" > /dev/null 2>&1 && echo true || echo false)
    local sync_home_to_workspace=false

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
            --no-activate-pyenv)
                activate_python_environment=false
                shift
                ;;
            --no-sync-pyenv)
                sync_python_environment=false
                shift
                ;;
            --force-sync-pyenv)
                sync_python_environment=true
                shift
                ;;
            --sync-home)
                sync_home_to_workspace=true
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
        # Ensure we have an up-to-date version of the CLI tool
        (cd /opt/vast-cli && git pull)
        # Attempt to upgrade Instance Portal to latest or specified version
        update-portal ${PORTAL_VERSION:+-v $PORTAL_VERSION}
        # Move /home to ${WORKSPACE}
        [[ "${sync_home_to_workspace}" = "true" ]] && sync_home
        # Move files from /opt/workspace-internal to ${WORKSPACE}
        sync_workspace
        # Prevent dubious ownership 
        set_git_safe_dirs
        # Sync Python environment if using volumes and not overridden
        [[ "${sync_python_environment}" = "true" ]] && sync_venv
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

        if [[ "$sync_home_to_workspace" = "true" && -d "${WORKSPACE}/home" ]] && grep -q "/etc/environment" "${WORKSPACE}/home/user/.bashrc"; then
            target_bashrc="/root/.bashrc"
        else
            target_bashrc="/root/.bashrc /home/user/.bashrc"
        fi
        # Ensure /etc/environment is sourced on login
        [[ "${export_env}" = "true" ]] &&  echo '. /etc/environment' | tee -a $target_bashrc
        # Ensure node npm (nvm) are available on login
        echo '. /opt/nvm/nvm.sh' | tee -a $target_bashrc
        # Ensure users are dropped into the venv on login.  Must be after /.launch has updated PS1
        if [[ "${activate_python_environment}" == "true" ]]; then
            echo 'cd ${WORKSPACE} && source /venv/${ACTIVE_VENV:-main}/bin/activate' | tee -a $target_bashrc
        fi
        # Warn CLI users if the container provisioning is not yet complete. Red >>>
        echo '[[ -f /.provisioning ]] && echo -e "\e[91m>>>\e[0m Instance provisioning is not yet complete.\n\e[91m>>>\e[0m Required software may not be ready.\n\e[91m>>>\e[0m See /var/log/portal/provisioning.log or the Instance Portal web app for progress updates\n\n"' | tee -a $target_bashrc
        touch /.first_boot_complete
    fi

    # Source the file at /etc/environment - We can now edit environment variables in a running instance
    [[ "${export_env}" = "true" ]] && . /etc/environment

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
    # Prevents dubious ownership issues
    find "${WORKSPACE}" -name ".git" | while read gitpath; do
        parent_dir=$(dirname "$gitpath")
        if ! grep -q "$parent_dir" /root/.gitconfig > /dev/null 2>&1; then
            git config --global --add safe.directory "$parent_dir"
        fi
        if ! grep -q "$parent_dir" /home/user/.gitconfig > /dev/null 2>&1; then
            sudo -u user bash -c "HOME=/home/user git config --global --add safe.directory $parent_dir"
        fi
    done
}

# Move /home into workspace
sync_home() {
    workspace=${WORKSPACE:-/workspace}
    # Use lockfile to prevent multiple instances syncing to a volume
    lockfile=${workspace}/.sync_home
    if [[ ! -f $lockfile ]]; then
        echo "Starting sync from C.${CONTAINER_ID}:/home" > $lockfile
        mv -n /home "${workspace}"
        echo "Complete!" >> $lockfile
    fi
    # Always symlink
    rm -rf /home > /dev/null 2>&1
    ln -s "${WORKSPACE}/home" /home
}

# Move workspace from image into container/volume
sync_workspace() {
    workspace=${WORKSPACE:-/workspace}
    # Use lockfile to prevent multiple instances syncing to a volume
    lockfile=${workspace}/.sync_content_${IMAGE_ID}
    if [[ ! -f $lockfile ]]; then
        echo "Starting sync from C.${CONTAINER_ID}:/opt/workspace-internal" > $lockfile
        mkdir -p "${workspace}/" > /dev/null 2>&1
        chown -f 0:1001 "${workspace}/" > /dev/null 2>&1
        chmod 2775 "${workspace}/" > /dev/null 2>&1
        setfacl -d -m g:1001:rwX "${workspace}/" > /dev/null 2>&1

        # Copy each item in /opt/workspace-internal and avoid clobbering user generated files if volume
        find /opt/workspace-internal -mindepth 1 -maxdepth 1 -print0 | while IFS= read -r -d '' item; do
        basename_item=$(basename "$item")
        target="${workspace}/${basename_item}"
        
        if [[ -d "$item" ]]; then
            # Copy directory recursively
            sudo -u user bash -c "umask 002 && cp -rf --update=none '$item' '${workspace}/'"
            # Apply ownership and permissions to the copied directory and its contents
            find "$target" -type d -exec chown 0:1001 {} \; -exec chmod 2775 {} \;
            find "$target" -type f -exec chown 0:1001 {} \; -exec chmod u+rw,g+rw,o+r {} \;
            setfacl -R -d -m g:1001:rwX "$target" > /dev/null
        elif [[ -f "$item" ]]; then
            # Copy file
            sudo -u user bash -c "umask 002 && cp --update=none '$item' '${workspace}/'"
            # Apply ownership to the copied file
            chown 0:1001 "$target"
            chmod u+rw,g+rw,o+r "$target"
        fi
    done
        echo "Complete!" >> $lockfile
    fi
    # Remove default files that do not belong in the workspace
    rm -f ${WORKSPACE}/onstart.sh ${WORKSPACE}/ports.log 
}

# Move the venvs from /venv/* to $workspace volume
sync_venv() {
    workspace=${WORKSPACE:-/workspace}
    lockfile=${workspace}/.sync_python_${IMAGE_ID}
    
    # Copy if no lock
    if [[ ! -f $lockfile ]]; then
        sudo -u user bash -c "umask 002 && cp -rf --update=none /.uv '${workspace}'"
        cp -rf --update=none /.uv "$workspace" 
        # Iterate through each directory in /venv and check if it's a virtual environment
        for dir in /venv/*/; do
            # Check if directory exists and contains pyvenv.cfg (indicating it's a venv)
            if [[ -d "$dir" && -f "${dir}pyvenv.cfg" ]]; then
                venv_name=$(basename "$dir")
                origin_path="/venv/${venv_name}"
                target_path="${workspace}/venv/${IMAGE_ID:-unspecified-image}/${venv_name}"
                
                echo "Starting python env sync from C.${CONTAINER_ID}:${target_path}" > $lockfile
                mkdir -p "$(dirname $target_path)"
                sudo -u user bash -c "umask 002 && cp -rf --update=none '/venv/${venv_name}' '${target_path}'"

                cd "$target_path"

                # Fix the activation script
                sed -i 's|VIRTUAL_ENV_PROMPT=$(basename "$VIRTUAL_ENV")|VIRTUAL_ENV_PROMPT="vol->$(basename $VIRTUAL_ENV)"|g' bin/activate                
            fi
        done
    fi
    
    # Delete and link always
    for dir in /venv/*/; do
        # Check if directory exists and contains pyvenv.cfg (indicating it's a venv)
        if [[ -d "$dir" && -f "${dir}pyvenv.cfg" ]]; then
            venv_name=$(basename "$dir")
            origin_path="/venv/${venv_name}"
            target_path="${workspace}/venv/${IMAGE_ID:-unspecified-image}/${venv_name}"
            rm -rf "$origin_path" > /dev/null 2>&1
            ln -s "$target_path" "$origin_path"
        fi
    done

    rm -rf /.uv >/dev/null 2>&1
    ln -s "${workspace}/.uv" /.uv
}

main "$@"