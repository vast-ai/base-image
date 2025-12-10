#!/bin/bash

# Sync Python and conda environments if using volumes and not overridden
sync_environment() {
    if [[ "${sync_environment}" = "true" ]]; then
        _sync_environment
    fi
}

# Move the environments from /venv/* & /conda/* to $workspace volume
_sync_environment() {
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

sync_environment