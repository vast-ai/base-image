#!/bin/bash
# Launch the instance test runner in the background when INSTANCE_TEST=true.
#
# Ordering matters: this is numbered 70 so it runs after 65-supervisor-launch.sh
# (the runner's early tests need supervisord) but BEFORE 75-provisioning-manifest.sh.
# The runner brings up its HTTP/SSE results server immediately, so a test client
# can connect within seconds of the instance reaching "running" and watch
# provisioning happen live — its 12-provisioning.sh test monitors /.provisioning
# until 95-supervisor-wait.sh clears it.
#
# If this ran after provisioning (it used to be 85-instance-test.sh), the results
# server would not exist until model downloads / pip installs finished — minutes
# to tens of minutes — during which a client has nothing to connect to, and
# 12-provisioning.sh's live monitoring would be dead code.
#
# IMPORTANT: This file is sourced (not executed) by boot_default.sh.
# Never use 'exit' here — it would abort the entire boot sequence,
# preventing 95-supervisor-wait.sh from removing /.provisioning.

if [[ "${INSTANCE_TEST,,}" != "true" ]]; then
    return 0
fi

echo "Starting instance test runner in background..."
/opt/instance-tools/tests/runner.sh --auto &
