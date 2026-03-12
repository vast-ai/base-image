#!/bin/bash
# Test: boot markers and config files are in expected state.
source "$(dirname "$0")/../lib.sh"

# Provisioning marker must be gone (runner waits for this, but verify)
[[ ! -f /.provisioning ]] || test_fail "/.provisioning still exists"

# First boot marker — informational, not required
if [[ -f /.first_boot_complete ]]; then
    echo "  /.first_boot_complete present"
fi

# Provisioning outcome markers (only if provisioning was configured)
if [[ -n "${PROVISIONING_MANIFEST:-}" ]] || [[ -f /provisioning.yaml ]]; then
    if [[ ! -f /.provisioning_complete ]] && [[ ! -f /.provisioning_failed ]]; then
        test_fail "provisioning was configured but neither /.provisioning_complete nor /.provisioning_failed exists"
    fi
fi

# /etc/environment must exist and contain PATH
assert_file_exists /etc/environment
grep -q "PATH=" /etc/environment || test_fail "/etc/environment missing PATH"

# /etc/portal.yaml — skip check in serverless if caddy not present
if ! is_serverless || [[ -f /etc/supervisor/conf.d/caddy.conf ]]; then
    assert_file_exists /etc/portal.yaml
fi

# /etc/Caddyfile — not expected in serverless
if ! is_serverless; then
    assert_file_exists /etc/Caddyfile
fi

test_pass "boot markers and configs in expected state"
