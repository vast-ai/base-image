#!/bin/bash
# Test: expected environment variables are set.
source "$(dirname "$0")/../lib.sh"

# WORKSPACE set and is a directory
assert_env_set WORKSPACE
assert_dir_exists "$WORKSPACE"

# Standard env vars
[[ "${PYTHONUNBUFFERED:-}" == "1" ]] || test_fail "PYTHONUNBUFFERED not set to 1"
[[ "${PIP_BREAK_SYSTEM_PACKAGES:-}" == "1" ]] || test_fail "PIP_BREAK_SYSTEM_PACKAGES not set to 1"
[[ "${NVIDIA_DRIVER_CAPABILITIES:-}" == "all" ]] || test_fail "NVIDIA_DRIVER_CAPABILITIES not set to all"

# PATH includes instance-tools
[[ ":${PATH}:" == *":/opt/instance-tools/bin:"* ]] || test_fail "PATH does not contain /opt/instance-tools/bin"

# UV_LINK_MODE (skip if uv not installed)
if command -v uv &>/dev/null; then
    [[ "${UV_LINK_MODE:-}" == "copy" ]] || test_fail "UV_LINK_MODE not set to copy"
fi

# /etc/environment sourceable and contains PATH
assert_file_exists /etc/environment
(source /etc/environment 2>/dev/null) || test_fail "/etc/environment is not sourceable"
grep -q "PATH=" /etc/environment || test_fail "/etc/environment missing PATH"

# HF_HOME should be set by boot scripts (10-prep-env.sh)
if [[ -n "${HF_HOME:-}" ]]; then
    echo "  HF_HOME=${HF_HOME}"
    [[ -d "$HF_HOME" ]] || echo "  WARN: HF_HOME directory does not exist yet"
elif is_vast_image; then
    test_fail "HF_HOME not set (required for IMAGE_TYPE=vast)"
else
    echo "  WARN: HF_HOME not set"
fi

# DATA_DIRECTORY should match WORKSPACE
if [[ -n "${DATA_DIRECTORY:-}" ]]; then
    echo "  DATA_DIRECTORY=${DATA_DIRECTORY}"
elif is_vast_image; then
    test_fail "DATA_DIRECTORY not set (required for IMAGE_TYPE=vast)"
else
    echo "  WARN: DATA_DIRECTORY not set"
fi

# Umask — Dockerfile sets 002 in .bashrc for group-writable files
tmpfile=$(mktemp -p "${WORKSPACE:-/tmp}" .umask-test-XXXXXX)
file_perms=$(stat -c '%a' "$tmpfile")
rm -f "$tmpfile"
echo "  new file permissions: ${file_perms} (umask $(umask))"
if [[ "$file_perms" == "664" ]]; then
    echo "  umask enforcement: correct (664)"
else
    echo "  WARN: expected 664, got ${file_perms} (umask may not be 002)"
fi

test_pass "environment variables verified"
