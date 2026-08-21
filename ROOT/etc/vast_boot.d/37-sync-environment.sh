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
            # Publish completion BEFORE clearing in-progress, so there is no
            # instant where neither marker is present on a finished tree.
            touch "${env_dir}/.synced"
            rm -f "${env_dir}/.syncing"
        fi
    fi

    # Wait until sync is complete, even if this instance is not syncing.
    #
    # This used to be `until [ ! -f "${env_dir}/.syncing" ]`, and absence of an
    # in-progress marker is NOT the same event as the lock. `mkdir "$env_dir"`
    # is atomic; the `touch` of the marker is a separate syscall after it. A
    # co-located instance sharing this volume that observed the directory
    # between the two found no marker, exited this loop on its first iteration,
    # and went straight to `rm -rf /venv/main; ln -s ...` — pointing
    # /venv/main at a tree the winner was still copying into. Everything from
    # boot stage 45 onward (bashrc, TLS cert gen, supervisord, every service,
    # provisioning) then runs against a dangling symlink with no python.
    #
    # Wait for a COMPLETION marker instead: "not started yet" and "finished" are
    # indistinguishable by absence, but presence of done is unambiguous.
    _sync_wait=0
    while [[ ! -f "${env_dir}/.synced" ]]; do
        # A tree written by an older image carries neither marker. Nothing in
        # progress plus a populated venv dir means that sync finished before
        # completion markers existed.
        if [[ ! -f "${env_dir}/.syncing" ]] && compgen -G "${venv_dir}/*" >/dev/null 2>&1; then
            # Retire the legacy shape once, so a pre-marker tree does not
            # re-take this branch on every boot forever. Best-effort: a
            # read-only volume just keeps using the fallback.
            touch "${env_dir}/.synced" 2>/dev/null || true
            break
        fi
        # Bounded, because .syncing is on a SHARED volume and an instance
        # destroyed mid-sync leaves it there permanently — every later instance
        # would block here forever at boot stage 37, before supervisord ever
        # launches: no portal, no services, and no failing check to point at.
        if (( _sync_wait >= 3600 )); then
            echo "ERROR: environment sync did not complete after ${_sync_wait}s."
            echo "  Refusing to relink /venv/* against a partial tree."
            # Two different shapes reach here and they need OPPOSITE remedies.
            # Saying "remove .syncing" for the second is actively harmful: it
            # makes a PARTIAL tree pass the legacy discriminator on the next
            # boot and get symlinked, which is the outcome this wait exists to
            # prevent.
            if [[ -f "${env_dir}/.syncing" ]]; then
                echo "  An instance died mid-sync and left ${env_dir}/.syncing on the"
                echo "  shared volume. The tree is PARTIAL: delete ${env_dir} entirely"
                echo "  so the next instance re-syncs it. Do not just remove the marker."
            else
                echo "  ${env_dir} exists with no markers and no content — an instance"
                echo "  died after taking the lock and before writing anything. Delete"
                echo "  ${env_dir} so the next instance can claim it."
            fi
            # Record it where a test can see it. boot_default.sh sources the
            # stages and DISCARDS their status, so a `return 1` here is otherwise
            # invisible to every detector in the image — which is why the SSH
            # stranding this path used to cause could only be found by reading
            # the code. Deliberate failures only: a blanket "any non-zero source"
            # wrapper would fire on 10-prep-env.sh, whose last line is a
            # legitimately-false conditional, and a check that cries wolf is
            # worse than none.
            echo "37-sync-environment: environment sync did not complete" >> /var/log/vast_boot_failures 2>/dev/null || true
            return 1
        fi
        echo "Waiting for environment to sync..."
        sleep 10
        _sync_wait=$((_sync_wait + 10))
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