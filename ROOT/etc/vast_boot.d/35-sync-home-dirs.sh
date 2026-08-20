#!/bin/bash

sync_home() {
    if [[ "${sync_home_to_workspace}" = "true" ]]; then
        _sync_home
    fi
}

# Move /home and /root into workspace
# Re-link the .ssh directories that the sync moved aside.
#
# _sync_home MOVES /root/.ssh and each /home/*/.ssh into ${ssh_home_dir} before
# the sync wait, and the symlinks that make them reachable are only created
# after it. Any exit between the two strands every key: boot_default.sh
# discards a stage's exit status, so boot continues, supervisord starts and the
# portal comes up while key-based SSH is dead — and 46-user-propagate-ssh-keys
# then dies on its first line (`realpath /root/.ssh/authorized_keys` under
# set -euo pipefail), so it does not even recreate an empty file.
_restore_ssh_links() {
    if [[ -d "${ssh_home_dir}/root/.ssh" && ! -e /root/.ssh ]]; then
        ln -sfn "${ssh_home_dir}/root/.ssh" /root/.ssh
    fi
    local d username
    for d in "${ssh_home_dir}"/*; do
        [[ -d "$d" ]] || continue
        username=$(basename "$d")
        [[ "$username" == "root" ]] && continue
        if [[ -d "${d}/.ssh" && -d "/home/${username}" && ! -e "/home/${username}/.ssh" ]]; then
            ln -sfn "${d}/.ssh" "/home/${username}/.ssh"
        fi
    done
}

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

            # Completion before clearing in-progress: no instant where a
            # finished tree carries neither marker.
            touch "${sync_home_dir}/.synced"
            rm -f "${sync_home_dir}/.syncing"
        fi
    fi

    # Wait until sync is complete.
    #
    # Same defect as 37-sync-environment, against /root and /home/* instead of
    # /venv: `mkdir` is the atomic lock, the `touch` is a separate syscall after
    # it, so a co-located instance sharing this volume could see the directory,
    # find no in-progress marker, and fall straight through to the symlinking
    # below while the winner was still running `mv /root ...`. Wait for the
    # completion marker, which is unambiguous.
    _home_wait=0
    while [[ ! -f "${sync_home_dir}/.synced" ]]; do
        # Written by an older image: neither marker, but the move is done.
        if [[ ! -f "${sync_home_dir}/.syncing" ]] && [[ -d "${sync_home_dir}/root" ]]; then
            # Retire the legacy shape once, so a pre-marker tree does not
            # re-take this branch on every boot forever. Best-effort: a
            # read-only volume just keeps using the fallback.
            touch "${sync_home_dir}/.synced" 2>/dev/null || true
            break
        fi
        # Bounded: .syncing lives on a SHARED volume, so an instance destroyed
        # mid-sync would otherwise block every later instance here forever, at
        # boot stage 35 — before supervisord launches.
        if (( _home_wait >= 3600 )); then
            echo "ERROR: home sync did not complete after ${_home_wait}s."
            echo "  Refusing to symlink home directories against a partial tree."
            # Two different shapes reach here and they need OPPOSITE remedies.
            # Saying "remove .syncing" for the second is actively harmful: it
            # makes a PARTIAL tree pass the legacy discriminator on the next
            # boot and get symlinked, which is the outcome this wait exists to
            # prevent.
            if [[ -f "${sync_home_dir}/.syncing" ]]; then
                echo "  An instance died mid-sync and left ${sync_home_dir}/.syncing on the"
                echo "  shared volume. The tree is PARTIAL: delete ${sync_home_dir} entirely"
                echo "  so the next instance re-syncs it. Do not just remove the marker."
            else
                echo "  ${sync_home_dir} exists with no markers and no content — an instance"
                echo "  died after taking the lock and before writing anything. Delete"
                echo "  ${sync_home_dir} so the next instance can claim it."
            fi
            # Put .ssh back before leaving. It was MOVED out to ${ssh_home_dir}
            # above — before this wait — and the symlinks that make it reachable
            # are below it. Returning between the two strands every key: boot
            # continues (boot_default discards a stage's status), supervisord
            # starts, the portal comes up, and the instance looks healthy with
            # key-based SSH dead and the customer's home content invisible. The
            # stale marker is on the SHARED volume, so a restart repeats it.
            _restore_ssh_links
            # Record it where a test can see it. boot_default.sh sources the
            # stages and DISCARDS their status, so a `return 1` here is otherwise
            # invisible to every detector in the image — which is why the SSH
            # stranding this path used to cause could only be found by reading
            # the code. Deliberate failures only: a blanket "any non-zero source"
            # wrapper would fire on 10-prep-env.sh, whose last line is a
            # legitimately-false conditional, and a check that cries wolf is
            # worse than none.
            echo "35-sync-home-dirs: home sync did not complete" >> /var/log/vast_boot_failures 2>/dev/null || true
            return 1
        fi
        sleep 10
        echo "Waiting for home to sync..."
        _home_wait=$((_home_wait + 10))
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
