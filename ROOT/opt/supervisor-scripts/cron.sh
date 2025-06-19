#!/bin/bash
set -m
SCRIPT_PID=$

cleanup() {
    kill -TERM -$SCRIPT_PID 2>/dev/null
    sleep 2
    kill -KILL -$SCRIPT_PID 2>/dev/null
    exit 0
}

trap cleanup EXIT INT TERM

cron -f 2>&1 | tee -a /var/log/portal/cron.log
