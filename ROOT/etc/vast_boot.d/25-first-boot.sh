#!/bin/bash

# Source any scripts that should only run on first boot

# On a serverless worker, skip the portal and vast-cli updates. Cold start is the product
# there and these are two pure-latency network fetches; the units they update never run,
# because exit_serverless.sh stops them.
#
# This lived in 01-detect-serverless.sh, the autoscaler-inference bridge, which is now
# deleted (ADR 0038 supersedes ADR 0034): the backend injects SERVERLESS itself, so the
# inference is redundant. The consequence outlives the inference and belongs here, next
# to the first_boot scripts that read these flags — `first_boot/05-update-vast.sh` and
# `first_boot/10-update-instance-portal.sh`.
#
# Deliberately LATER than the old stage 01: 10-prep-env.sh has sourced /etc/environment
# by now, so a SERVERLESS set by hand in that file is honoured here where it was invisible
# to stage 01. Both flags are main()'s locals, in dynamic scope because boot_default.sh
# SOURCES these stages — the mechanism 10-prep-env.sh and 37-sync-environment.sh already
# rely on.
if [[ "${SERVERLESS,,}" == "true" ]]; then
    update_portal=false
    update_vast_cli=false
fi

if [[ ! -f /.first_boot_complete ]]; then
    for script in /etc/vast_boot.d/first_boot/*.sh; do
        [[ -f "$script" ]] && [[ -r "$script" ]] && . "$script"
    done

    touch /.first_boot_complete
fi
