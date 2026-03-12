#!/bin/bash
# Launch the instance test runner in the background when INSTANCE_TEST=true.
# The runner has its own provisioning wait loop, HTTP results server, and EXIT trap.

if [[ "${INSTANCE_TEST,,}" != "true" ]]; then
    exit 0
fi

echo "Starting instance test runner in background..."
/opt/instance-tools/tests/runner.sh &
