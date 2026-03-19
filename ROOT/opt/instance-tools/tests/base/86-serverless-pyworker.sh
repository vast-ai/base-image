#!/bin/bash
# Test: serverless mode — verify pyworker starts and port 3000 is exposed.
source "$(dirname "$0")/../lib.sh"

is_serverless || test_skip "not in serverless mode"

# pyworker should be running (or at least attempted)
assert_service_running "pyworker"
echo "  pyworker: RUNNING"

# pyworker exposes its HTTP handler on port 3000
if wait_for_port 3000 60; then
    echo "  pyworker: port 3000 listening"
else
    test_fail "pyworker port 3000 not listening after 60s"
fi

test_pass "serverless pyworker checks passed"
