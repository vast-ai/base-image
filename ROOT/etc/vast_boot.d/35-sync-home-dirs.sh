#!/bin/bash

sync_home() {
    if [[ "${sync_home_to_workspace}" = "true" ]]; then
        _sync_home
    fi
}

# Move /home and /root into workspace
_sync_home() {
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

sync_home
