#!/bin/bash
# Test: cron service is running (runs in all modes including serverless).
source "$(dirname "$0")/../lib.sh"

# cron supervisor service
if [[ -f /etc/supervisor/conf.d/cron.conf ]]; then
    assert_service_running "cron"
fi

# cron daemon PID
pidof cron &>/dev/null || test_fail "cron daemon not running"

# crontab command available
assert_command_exists crontab

test_pass "cron running"
