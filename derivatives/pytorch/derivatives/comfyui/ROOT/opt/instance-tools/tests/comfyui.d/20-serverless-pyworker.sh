#!/bin/bash
# Test: serverless mode — the pyworker starts and its HTTP handler is on :3000.
#
# THIS LIVES IN THE ENGINE SUITE, NOT IN base/, AND THAT IS THE POINT.
# The base image ships pyworker.sh, but that only bootstraps a worker — what
# actually binds :3000 is the inference engine sitting behind it, which the base
# image does not have. As a base/ test this could never hold on a bare image:
# measured live on a driver-610 host, `pyworker: RUNNING` followed by
# `port 3000 not listening after 60s`. The real cost was not the red — it was
# that base-qa could then never set SERVERLESS=true, so this test and its
# sibling 85 had never executed once, anywhere.
#
# 85 stayed in base/: "the non-serverless services are stopped and their ports
# are closed" is a property the base image genuinely owns. This one is not.
#
# The `is_serverless` guard below keeps it dormant until a template turns
# serverless on, so it is inert (not skip-as-pass — there is nothing to assert
# when the mode is off) on today's engine QA cells and activates by itself when
# the serverless templates land. Enforced by linter rule L067.

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
