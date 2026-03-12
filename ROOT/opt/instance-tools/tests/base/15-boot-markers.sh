#!/bin/bash
# Test: boot markers and config files are in expected state.
source "$(dirname "$0")/../lib.sh"

# Provisioning marker should be gone (test 12-provisioning monitors this)
if [[ -f /.provisioning ]]; then
    echo "  WARN: /.provisioning still exists"
fi

# First boot marker — informational, not required
if [[ -f /.first_boot_complete ]]; then
    echo "  /.first_boot_complete present"
fi

# Provisioning outcome markers — informational (12-provisioning validates these)
for marker in /.provisioning_complete /.provisioning_failed; do
    if [[ -f "$marker" ]]; then
        echo "  present: $marker"
    fi
done

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
