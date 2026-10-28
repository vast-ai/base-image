#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

# Opt-in: a scripted run (an agent, a benchmark) sets GRPO_AUTORUN=1 and reads results.json;
# a person opens the notebook instead. It has no port, so there is no portal gate.
if [[ "${GRPO_AUTORUN:-0}" != "1" ]]; then
    echo "GRPO_AUTORUN is not 1; nothing to do"
    exit 0
fi

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until provisioning completes (/.provisioning present)"
    sleep 5
done

cd "${WORKSPACE}/grpo" || { echo "${WORKSPACE}/grpo missing"; exit 1; }

# Once per instance: a restart or a supervisor restart must not start a second run.
if [[ -f .autorun-started ]]; then
    echo "autorun already started on this instance (.autorun-started); not starting another"
    exit 0
fi
touch .autorun-started

/venv/main/bin/python train_grpo.py ${GRPO_ARGS:-} 2>&1 | tee -a autorun.log
