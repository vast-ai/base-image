#!/bin/bash
# Launch the instance test runner in the background when INSTANCE_TEST=true.
# The runner has its own provisioning monitoring, HTTP results server, and EXIT trap.
#
# IMPORTANT: This file is sourced (not executed) by boot_default.sh.
# Never use 'exit' here — it would abort the entire boot sequence,
# preventing 95-supervisor-wait.sh from removing /.provisioning.

if [[ "${INSTANCE_TEST,,}" != "true" ]]; then
    return 0
fi

echo "Starting instance test runner in background..."
/opt/instance-tools/tests/runner.sh --auto &
