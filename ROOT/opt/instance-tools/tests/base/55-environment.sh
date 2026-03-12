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

test_pass "environment variables verified"
