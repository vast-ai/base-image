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

# Umask — 45-user-write-bashrc sets 002 in .bashrc for group-writable files.
#
# The umask that matters is the one an INTERACTIVE shell gets, because that is
# where it is set and what a customer's session inherits: 45-user-write-bashrc
# writes it into /root/.bashrc (and /home/user/.bashrc). boot_default.sh's
# `umask 002` on line 3 is convenience for that shell only.
#
# This used to create a temp file from THIS process and expect 664. The runner
# execs each test as `env -u SHELLOPTS bash <test>` — non-interactive, no rc
# sourced — so it observed bash's 022 default and reported 600 on a perfectly
# correct image. It WARNed on every cell of every run and could never pass.
_login_umask=$(bash -ic 'umask' 2>/dev/null | tr -d '[:space:]')
if [[ "$_login_umask" == "0002" ]]; then
    echo "  interactive umask: ${_login_umask} (group-writable, as configured)"
else
    fail_later "umask" "interactive shell umask is ${_login_umask:-unknown}, expected 0002 — check the bashrc written by 45-user-write-bashrc"
fi
echo "  (this test process: $(umask) — non-interactive, no rc sourced, so not the configured value)"

report_failures

test_pass "environment variables verified"
