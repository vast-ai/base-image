#!/bin/bash
# Test: networking tools and basic connectivity.
source "$(dirname "$0")/../lib.sh"

# Required commands
for cmd in curl wget ss ip ping nc dig; do
    assert_command_exists "$cmd"
done

# Loopback interface
ip link show lo &>/dev/null || test_fail "loopback interface not found"

# localhost resolves (may be 127.0.0.1 or ::1 depending on container config)
getent hosts localhost &>/dev/null || test_fail "cannot resolve localhost"

# External DNS resolution
if dig +short +timeout=5 vast.ai 2>/dev/null | grep -qE '^[0-9]+\.[0-9]+'; then
    echo "  external DNS: vast.ai resolves"
else
    echo "  WARN: external DNS resolution failed (dig vast.ai)"
fi

report_failures

test_pass "networking tools present, connectivity ok"
