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

# /etc/portal.yaml — not expected in serverless mode
if ! is_serverless; then
    assert_file_exists /etc/portal.yaml
fi

# /etc/Caddyfile — not expected in serverless; may take a few seconds to generate
if ! is_serverless; then
    for _ in $(seq 1 30); do
        [[ -f /etc/Caddyfile ]] && break
        sleep 1
    done
    assert_file_exists /etc/Caddyfile
fi

test_pass "boot markers and configs in expected state"
