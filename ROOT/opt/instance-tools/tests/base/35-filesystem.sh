#!/bin/bash
# Test: expected filesystem paths, directories, and supervisor script/conf pairing.
source "$(dirname "$0")/../lib.sh"

# Core directories that must always exist
assert_dir_exists /opt/instance-tools/bin
assert_dir_exists /opt/supervisor-scripts
assert_dir_exists /etc/supervisor/conf.d

# /var/log/portal with sticky+world-writable perms
assert_dir_exists /var/log/portal
assert_file_mode /var/log/portal 1777

# Workspace
assert_dir_exists "${WORKSPACE:-/workspace}"

# Skip-if-absent paths — check what IS present, don't fail on missing
for path in \
    /opt/portal-aio \
    /opt/syncthing \
    /opt/nvm \
    /opt/miniforge3 \
    /venv/main/bin/python \
    /opt/portal-aio/venv/bin/python \
    /opt/instance-tools/provisioner/venv/bin/python; do
    if [[ -e "$path" ]]; then
        echo "  present: $path"
    else
        echo "  absent (ok): $path"
    fi
done

# For each supervisor script, verify matching .conf exists
for script in /opt/supervisor-scripts/*.sh; do
    [[ -f "$script" ]] || continue
    name=$(basename "$script" .sh)
    # Skip utils directory scripts and helper files
    [[ "$name" == "utils" ]] && continue
    [[ -d "/opt/supervisor-scripts/$name" ]] && continue
    if [[ ! -f "/etc/supervisor/conf.d/${name}.conf" ]]; then
        echo "  WARN: supervisor script ${name}.sh has no matching .conf"
    fi
done

test_pass "filesystem structure verified"
