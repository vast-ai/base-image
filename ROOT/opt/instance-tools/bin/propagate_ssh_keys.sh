#!/bin/bash

# Ensure uploaded keys are valid for user account if account doesn't already have a key
# Currently we dont have per-user keys or even multi-user functionality, so this is a useful workaround.

set -euo pipefail

ROOT_AUTH_KEYS="/root/.ssh/authorized_keys"
SSH_DIR_PERMISSIONS="700"
AUTH_KEYS_PERMISSIONS="600"

# Function to check if a key exists in a file
key_exists() {
    local key="$1"
    local file="$2"
    grep -qF "$key" "$file"
}

# Function to ensure directory exists with correct permissions
ensure_dir() {
    local dir="$1"
    local permissions="$2"
    if [[ ! -d "$dir" ]]; then
        mkdir -p "$dir"
        chmod "$permissions" "$dir"
    fi
}

# Function to get user's primary group
get_primary_group() {
    local username="$1"
    id -gn "$username"
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root" 
   exit 1
fi

# Check if root's authorized_keys file exists
if [[ ! -f "$ROOT_AUTH_KEYS" ]]; then
    echo "Root's authorized_keys file not found at $ROOT_AUTH_KEYS"
    exit 1
fi

# Ensure root keys have corect permissions (failsafe)
chown -R 0:0 /root/.ssh/
chmod $SSH_DIR_PERMISSIONS /root/.ssh
chmod $AUTH_KEYS_PERMISSIONS /root/.ssh/authorized_keys

# Iterate over users in /home
for user_home in /home/*; do
    if [[ ! -d "$user_home" ]]; then
        continue
    fi

    username=$(basename "$user_home")
    user_group=$(get_primary_group "$username")
    user_ssh_dir="$user_home/.ssh"
    user_auth_keys="$user_ssh_dir/authorized_keys"

    # Ensure .ssh directory exists with correct permissions
    ensure_dir "$user_ssh_dir" "$SSH_DIR_PERMISSIONS"
    chown "$username:$user_group" "$user_ssh_dir"

    # Create authorized_keys file if it doesn't exist
    if [[ ! -f "$user_auth_keys" ]]; then
        touch "$user_auth_keys"
        chmod "$AUTH_KEYS_PERMISSIONS" "$user_auth_keys"
        chown "$username:$user_group" "$user_auth_keys"
    fi

    # Check and add root's keys
    while IFS= read -r key || [ -n "$key" ]; do
    if ! key_exists "$key" "$user_auth_keys"; then
        echo "$key" >> "$user_auth_keys"
        echo "Added key for $username"
    fi
    done < "$ROOT_AUTH_KEYS"

    # Ensure correct permissions
    chmod "$AUTH_KEYS_PERMISSIONS" "$user_auth_keys"
    chown "$username:$user_group" "$user_auth_keys"
done

echo "SSH key propagation complete." | tee -a /var/log/portal/ssh-keys.log