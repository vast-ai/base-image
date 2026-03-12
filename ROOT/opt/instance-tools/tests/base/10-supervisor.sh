#!/bin/bash
# Test: supervisord process is alive.
# Service state checks are in 65-conditional-services.sh (after provisioning).
source "$(dirname "$0")/../lib.sh"

pgrep -f supervisord &>/dev/null || test_fail "supervisord not running"

# Verify supervisorctl can communicate (exit code 3 = some processes not running, which is fine)
supervisorctl status &>/dev/null; rc=$?
[[ $rc -le 3 ]] || test_fail "supervisorctl cannot communicate with supervisord (exit ${rc})"

test_pass "supervisord running"
