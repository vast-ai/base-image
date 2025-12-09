#!/bin/bash

# Hotfix enablement
# Very early run allows modification of any part of the startup routine.
# Use to mitigate any broken container build

# Script must manage its own run conditions

if [[ -n $HOTFIX_SCRIPT ]]; then
    curl -L -o /tmp/hotfix.sh "$HOTFIX_SCRIPT" && \
    chmod +x /tmp/hotfix.sh && \
    dos2unix /tmp/hotfix.sh && \
    echo "Applying hotfix script" && \
    /tmp/hotfix.sh | tee -a /var/log/hotfix.log
fi