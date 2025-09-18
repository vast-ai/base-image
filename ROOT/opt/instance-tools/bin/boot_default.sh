#!/bin/bash

umask 002
main() {
    local propagate_user_keys=true
    local export_env=true
    local generate_tls_cert=true
    local activate_python_environment=true
    local sync_environment=false
    local sync_home_to_workspace=false
    local update_portal=true
    local update_vast_cli=true

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
            --no-update-portal)
                update_portal=false
                shift
                ;;
            --no-activate-pyenv)
                activate_python_environment=false
                shift
                ;;
            --sync-environment)
                sync_environment=true
                shift
                ;;
            --sync-home)
                sync_home_to_workspace=true
                shift
                ;;
            --jupyter-override)
                export JUPYTER_OVERRIDE=true
                shift
                ;;
            *)
                echo "Warning: Unknown flag: $1" >&2
                shift
                ;;
        esac
    done  

    # Serverless optimizations
    if [[ "${SERVERLESS,,}" = "true" ]]; then
        update_portal=false
        update_vast_cli=false
    fi

    mkdir -p "${WORKSPACE}"
    cd "${WORKSPACE}"

    # Remove Jupyter from the portal config if no port or running in SSH only mode
    if [[ -z "${VAST_TCP_PORT_8080}" ]] || { [[ -f /.launch ]] && ! grep -qi jupyter /.launch && [[ "${JUPYTER_OVERRIDE,,}" != "true" ]]; }; then
        PORTAL_CONFIG=$(echo "$PORTAL_CONFIG" | tr '|' '\n' | grep -vi jupyter | tr '\n' '|' | sed 's/|$//')
    fi

    # Ensure correct port mappings for Jupyter when running in Jupyter launch mode
    if [[ -f /.launch ]] && grep -qi jupyter /.launch && [[ "${JUPYTER_OVERRIDE,,}" != "true" ]]; then
        PORTAL_CONFIG="$(echo "$PORTAL_CONFIG" | sed 's#localhost:8080:18080#localhost:8080:8080#g')"
    fi

    # Set HuggingFace home
    export HF_HOME=${HF_HOME:-${WORKSPACE}/.hf_home}
    mkdir -p "$HF_HOME"

    # Ensure environment contains instance ID (snapshot aware)
    instance_identifier=$(echo "${CONTAINER_ID:-${VAST_CONTAINERLABEL:-${CONTAINER_LABEL:-}}}")
    message="# Template controlled environment for C.${instance_identifier}"
    if [[ -z "${instance_identifier:-}" ]] || ! grep -q "$message" /etc/environment; then
        echo "$message" > /etc/environment
        echo 'PATH="/opt/instance-tools/bin:/usr/local/nvidia/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"' \
            >> /etc/environment
        env -0 | grep -zEv "^(HOME=|SHLVL=)|CONDA" | while IFS= read -r -d '' line; do
                name=${line%%=*}
                value=${line#*=}
                printf '%s="%s"\n' "$name" "$value"
            done >> /etc/environment
    fi

    # First run only...
    if [[ "${SERVERLESS,,}" != "true"  && ! -f /.first_boot_complete ]]; then
        echo "Applying first boot optimizations..."
        # Ensure we have an up-to-date version of the CLI tool
        if [[ "$update_vast_cli" = "true" ]]; then
            (cd /opt/vast-cli && git pull)
        fi
        # Attempt to upgrade Instance Portal to latest or specified version
        if [[ "$update_portal" = "true" ]]; then
            update-portal ${PORTAL_VERSION:+-v $PORTAL_VERSION}
        fi
        touch /.first_boot_complete
    fi

    # Move /home to ${WORKSPACE}
    [[ "${sync_home_to_workspace}" = "true" ]] && sync_home
    # Move files from /opt/workspace-internal to ${WORKSPACE}
    sync_workspace
    # Prevent dubious ownership 
    set_git_safe_dirs

    # Sync Python and conda environments if using volumes and not overridden
    [[ "${sync_environment}" = "true" ]] && sync_environment
    # Initial venv backup - Also runs as a cron job every 30 minutes
    /opt/instance-tools/bin/venv-backup.sh
        
    if ! grep -q "### Entrypoint setup ###" /root/.bashrc > /dev/null 2>&1; then
        target_bashrc="/root/.bashrc /home/user/.bashrc"
        echo "### Entrypoint setup ###" | tee -a $target_bashrc
        # Ensure /etc/environment is sourced on login
        [[ "${export_env}" = "true" ]] && { echo 'set -a'; echo '. /etc/environment'; echo '[[ -f "${WORKSPACE}/.env" ]] && . "${WORKSPACE}/.env"'; echo 'set +a'; } | tee -a $target_bashrc
        # Ensure node npm (nvm) are available on login
        echo '. /opt/nvm/nvm.sh' | tee -a $target_bashrc
        # Ensure users are dropped into the venv on login.  Must be after /.launch has updated PS1
        if [[ "${activate_python_environment}" == "true" ]]; then
            echo 'cd ${WORKSPACE} && source /venv/${ACTIVE_VENV:-main}/bin/activate' | tee -a $target_bashrc
        fi
        # Warn CLI users if the container provisioning is not yet complete. Red >>>
        echo '[[ -f /.provisioning ]] && echo -e "\e[91m>>>\e[0m Instance provisioning is not yet complete.\n\e[91m>>>\e[0m Required software may not be ready.\n\e[91m>>>\e[0m See /var/log/portal/provisioning.log or the Instance Portal web app for progress updates\n\n"' | tee -a $target_bashrc
        echo "### End entrypoint setup ###" | tee -a $target_bashrc
    fi
    
    # Avoid missing cuda libs error (affects 12.4)
    if [[ ! -e /usr/lib/x86_64-linux-gnu/libcuda.so && -e /usr/lib/x86_64-linux-gnu/libcuda.so.1 ]]; then \
        ln -s /usr/lib/x86_64-linux-gnu/libcuda.so.1 /usr/lib/x86_64-linux-gnu/libcuda.so; \
    fi

    # Let the 'user' account connect via SSH
    [[ "${propagate_user_keys}" = "true" ]] && /opt/instance-tools/bin/propagate_ssh_keys.sh

    # Source the file at /etc/environment - We can now edit environment variables in a running instance
    [[ "${export_env}" = "true" ]] && { set -a; . /etc/environment 2>/dev/null; . "${WORKSPACE}/.env" 2>/dev/null; set +a; }

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

    # Hotfix enablement
    # Allows modifying the environment before supervisor launches main processes.
    # Use this to assist user in fixing broken container.  Add in env or /etc/environment
    # This will run on every boot.  Script must handle its own run conditions
    if [[ -n $HOTFIX_SCRIPT ]]; then
        curl -L -o /tmp/hotfix.sh "$HOTFIX_SCRIPT" && \
        chmod +x /tmp/hotfix.sh && \
        dos2unix /tmp/hotfix.sh && \
        echo "Applying hotfix script" && \
        /tmp/hotfix.sh
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
        dos2unix /provisioning.sh && \
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

# Move /home and /root into workspace
sync_home() {
    workspace="${WORKSPACE:-/workspace}"
    sync_home_dir="${workspace}/home"
    ssh_home_dir="/home_ssh"
    mkdir -m 755 -p "${ssh_home_dir}"

    
    # Move .ssh dir out of home and symlink back before sync
    # This is required for non-POSIX network volumes 
    # Allows SSH configuration per-instance even when synchronized
    for user_dir in /home/*; do
        if [[ -d "$user_dir" && ! -L "$user_dir" ]]; then
            username=$(basename "$user_dir")
            ssh_original_path="${user_dir}/.ssh"
            ssh_preservation_dir="${ssh_home_dir}/${username}"
            ssh_preserved_path="${ssh_preservation_dir}/.ssh"
            # Create directory to store SSH data
            mkdir -m 700 -p "${ssh_preservation_dir}"
            chown "${username}:root" "${ssh_preservation_dir}"
            # Ensure SSH directory is present
            mkdir -m 700 -p "${ssh_original_path}"
            
            mv "${ssh_original_path}" "${ssh_preserved_path}"
            chmod 700 "${ssh_preserved_path}"
        fi
    done
    # Handle root user specially
    if [[ -d /root && ! -L /root ]]; then
        # Ensure SSH directory is present
        mkdir -m 700 -p "/root/.ssh"
        mkdir -m 700 -p "${ssh_home_dir}/root"
        mv /root/.ssh "${ssh_home_dir}/root/.ssh"
        chmod 700 "${ssh_home_dir}/root/.ssh"
    fi

    # Move special files
    [[ -f /root/onstart.sh ]] && mv /root/onstart.sh /onstart.sh 2>/dev/null
    ln -sf /onstart.sh /root/onstart.sh 2>/dev/null

    [[ -f /root/.vast_containerlabel ]] && mv /root/.vast_containerlabel /etc/.vast_containerlabel 2>/dev/null
    ln -sf /etc/.vast_containerlabel /root/.vast_containerlabel 2>/dev/null
    
    [[ -f /root/ports.log ]] && cp /root/ports.log /var/log/vast_ports.log 2>/dev/null
    ln -sf /var/log/vast_ports.log /root/ports.log 2>/dev/null

    [[ -f /root/.vast_api_key ]] && mv /root/.vast_api_key /etc/.vast_api_key 2>/dev/null
    ln -sf /etc/.vast_api_key /root/.vast_api_key 2>/dev/null

    # Move the home directories
    if [[ ! -d "$sync_home_dir" ]]; then
        # Atomic lock - Create it or wait for the other creator
        if mkdir "${sync_home_dir}"; then
            touch "${sync_home_dir}/.syncing"
            mkdir -p "${ssh_home_dir}"
            chmod 755 "${ssh_home_dir}"
            
            # Move root directory
            mv /root "${sync_home_dir}"

            # Move user directories
            for user_dir in /home/*; do
                mv "${user_dir}" "${sync_home_dir}"
            done

            rm -f "${sync_home_dir}/.syncing"
        fi
    fi

    # Wait until sync is complete
    echo "Waiting for environment to sync..."
    until [ ! -f "${sync_home_dir}/.syncing" ]; do
        sleep 10
        echo "Waiting for home to sync..."
    done

    # Always symlink
    if [[ -d ${sync_home_dir}/root ]]; then
        rm -rf /root > /dev/null 2>&1
        ln -sfn "${sync_home_dir}/root" /root
        
        # Link .ssh from container filesystem
        if [[ -d "${ssh_home_dir}/root/.ssh" ]]; then
            ln -sfn "${ssh_home_dir}/root/.ssh" /root/.ssh
        fi
    fi
    
    # Symlink each dir in sync_home_dir to /home/dir but exclude root
    for dir in "${sync_home_dir}"/*; do
        if [[ -d "$dir" && "$(basename "$dir")" != "root" ]]; then
            username=$(basename "$dir")
            rm -rf "/home/${username}" > /dev/null 2>&1
            ln -sfn "$dir" "/home/${username}"
            
            # Link .ssh from container filesystem
            if [[ -d "${ssh_home_dir}/${username}/.ssh" ]]; then
                ln -sfn "${ssh_home_dir}/${username}/.ssh" "/home/${username}/.ssh"
            fi
        fi
    done
    
    # Remove unnecessary entries from .bashrc
    sed -i -E '/^(DIRECT_PORT_START|DIRECT_PORT_END|VAST_CONTAINERLABEL)=/d' /root/.bashrc
}

# Move workspace from image into container/volume only if the target does not exist
sync_workspace() {
    workspace=${WORKSPACE:-/workspace}
    
    mkdir -p "${workspace}/" > /dev/null 2>&1
    
    # Copy each item in /opt/workspace-internal and avoid clobbering user generated files if volume
    find /opt/workspace-internal -mindepth 1 -maxdepth 1 -print0 | while IFS= read -r -d '' item; do
        basename_item=$(basename "$item")
        target="${workspace}/${basename_item}"
        
        # Create item-specific lock file for fine-grained locking
        lockfile="${workspace}/.sync_${basename_item}.lock"
        
        # Use flock for this specific item - wait indefinitely for the lock
        (
            # First try with timeout, then wait indefinitely if needed
            if ! flock -x -w 10 9; then
                echo "Lock busy for ${basename_item}, waiting for completion..." >&2
                # Wait indefinitely for the lock (blocks until available)
                flock -x 9
            fi
            
            if [[ -d "$item" && ! -e "$target" ]]; then
                # Copy directory recursively
                cp -ru "$item" "${workspace}/"
                # Apply ownership and permissions to the copied directory and its contents
            elif [[ -f "$item" && ! -e "$target" ]]; then
                # Copy file
                cp -f "$item" "${workspace}/"
            fi
            
        ) 9>"$lockfile"
    done

    # Remove default files that do not belong in the workspace (no lock needed for removal)
    rm -f "${workspace}/onstart.sh" "${workspace}/ports.log"
}

# Move the environments from /venv/* & /conda/* to $workspace volume
sync_environment() {
    workspace=${WORKSPACE:-/workspace}
    sync_dir="${workspace}/.environment_sync"
    env_id=${ENV_ID:-$(cat /.env_hash)}
    env_dir="${sync_dir}/${env_id}"
    venv_dir="${env_dir}/venv"
    uv_dir="${env_dir}/uv"
    nvm_dir="${env_dir}/nvm"
    
    mkdir -p "${sync_dir}"
    # Copy if not present
    if [[ ! -d "$env_dir" ]]; then
        # Atomic lock
        if mkdir "$env_dir"; then
            touch "${env_dir}/.syncing"
            mkdir -p "$venv_dir" "$uv_dir" "$nvm_dir"
            # Archive .uv directory if it exists
            if [[ -d "/.uv" ]]; then
                echo "Archiving .uv to ${uv_dir}"
                tar -czf uv.tar.gz -C /.uv .
                tar -xzf uv.tar.gz -C "${uv_dir}"
                rm -f uv.tar.gz
            fi
            
            # Copy NVM if it exists
            if [[ -d "/opt/nvm" ]]; then
                echo "Archiving NVM to ${nvm_dir}"
                tar -czf nvm.tar.gz -C /opt/nvm .
                tar -xzf nvm.tar.gz -C "${nvm_dir}"
                rm -f nvm.tar.gz
            fi
            
            # Handle venv directories
            for dir in /venv/*/; do
                # Check if directory exists and is a venv/conda env
                if [[ -d "$dir" && (-f "${dir}pyvenv.cfg" || -d "${dir}conda-meta") ]]; then
                    venv_name=$(basename "$dir")
                    origin_path="/venv/${venv_name}"
                    target_path="${venv_dir}/${venv_name}"

                    # Basic venv
                    if [[ -f "${dir}pyvenv.cfg" ]]; then
                        echo "Archiving venv ${venv_name} to ${target_path}"
                        mkdir -p "${target_path}"
                        tar -czf "${venv_name}.tar.gz" -C "/venv/${venv_name}" .
                        tar -xzf "${venv_name}.tar.gz" -C "${target_path}"
                        rm -f "${venv_name}.tar.gz"
                    else
                    # Conda
                        mkdir -p "$target_path"
                        if [[ -f "${origin_path}/bin/activate" ]]; then
                            mv -f "${origin_path}/bin/activate" "${origin_path}/bin/activate.orig"
                        fi
                        conda-pack --ignore-missing-files -j -1 -p "$origin_path" -d "$target_path" -o "${venv_name}.tar.gz"
                        echo "moving ./${venv_name}.tar.gz to $target_path"
                        mv "${venv_name}.tar.gz" "$target_path"
                        tar -xvf "${target_path}/${venv_name}.tar.gz" -C "$target_path"
                        rm -f "${target_path}/${venv_name}.tar.gz"
                        if [[ -f "${target_path}/bin/activate.orig" ]]; then
                            mv -f "${target_path}/bin/activate.orig" "${target_path}/bin/activate"
                        fi
                    fi

                    cd "$target_path"
                
                fi
            done
            rm -f "${env_dir}/.syncing"
        fi
    fi

    # Wait until sync is complete, even if this instance is not syncing
    echo "Waiting for environment to sync..."
    until [ ! -f "${env_dir}/.syncing" ]; do
        sleep 10
        echo "Waiting for environment to sync..."
    done

    # Delete and link venv directories
    for dir in /venv/*/; do
        # Check if directory exists and is a venv/conda env
        if [[ -d "$dir" && (-f "${dir}pyvenv.cfg" || -d "${dir}conda-meta") ]]; then
            venv_name=$(basename "$dir")
            origin_path="/venv/${venv_name}"
            target_path="${venv_dir}/${venv_name}"
            rm -rf "$origin_path" > /dev/null 2>&1
            ln -s "$target_path" "$origin_path"
        fi
    done

    rm -rf /.uv >/dev/null 2>&1
    ln -s "${uv_dir}" /.uv
    
    # Handle NVM symlink
    if [[ -d "${nvm_dir}" ]]; then
        rm -rf /opt/nvm >/dev/null 2>&1
        ln -s "${nvm_dir}" /opt/nvm
    fi
}

main "$@"