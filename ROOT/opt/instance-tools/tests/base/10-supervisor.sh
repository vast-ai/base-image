#!/bin/bash
# Test: supervisord is SERVING — not merely forked.
# Service state checks are in 65-conditional-services.sh (after provisioning).
#
# This is the first test in the suite, and it runs while supervisord is still
# starting: 65-supervisor-launch.sh backgrounds supervisord, then boot moves
# straight on to 70-instance-test.sh, which backgrounds this runner. The two are
# effectively simultaneous.
#
# It used to gate on `pgrep -f supervisord` and then call supervisorctl on the
# next line. Presence is true the instant supervisord forks; the RPC socket every
# other service assertion needs appears later. Measured in the shipped image,
# idle, 16 cores: presence at 1.7ms, socket usable at 383ms. On a QA host still
# provisioning, that gap was wide enough to fail this test 0.09s into the suite
# with `supervisorctl cannot communicate with supervisord (exit 4)`, taking
# 20-portal and 26-caddy-auth down as collateral and blocking a whole promote
# batch — on an image the same suite proved healthy 53 seconds later (ADR 0029).
source "$(dirname "$0")/../lib.sh"

if ! wait_for_supervisor 60; then
    # Only NOW is presence worth asking about, and only to say which failure it
    # is: never started, or started and never served.
    pgrep -f supervisord &>/dev/null \
        || test_fail "supervisord is not running at all"
    test_fail "supervisord is running but its RPC socket never came up after 60s"
fi

test_pass "supervisord running and serving on its RPC socket"
