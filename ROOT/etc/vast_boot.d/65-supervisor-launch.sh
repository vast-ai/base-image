#!/bin/bash

# Indicator for supervisor scripts to prevent launch during provisioning if necessary (if [[ -f /.provisioning ]] ...)
touch /.provisioning

# Now we run supervisord - Put it in the background so provisioning can be monitored in Instance Portal
supervisord \
    -n \
    -u root \
    -c /etc/supervisor/supervisord.conf &
supervisord_pid=$!