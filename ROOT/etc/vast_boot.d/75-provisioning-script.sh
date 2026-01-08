#!/bin/bash

# Provision the instance with a remote script - This will run on every startup until it has successfully completed without errors
# This is for configuration of existing images and will also allow for templates to be created without building docker images
# Experienced users will be able to convert the script to Dockerfile RUN and build a self-contained image
# NOTICE: If the provisioning script introduces new supervisor processes it must:
# - run `supervisorctl reread && supervisorctl update`

if [[ -n $PROVISIONING_SCRIPT && ! -f /.provisioning_complete ]]; then
    echo "*****"
    echo "*"
    echo "*"
    echo "* Provisioning instance with remote script from ${PROVISIONING_SCRIPT}"
    echo "*"
    echo "* This may take a while.  Some services may not start until this process completes."
    echo "* To change this behavior you can edit or remove the PROVISIONING_SCRIPT environment variable."
    echo "*"
    echo "*"
    echo "*****"
    # Only download it if we don't already have it - Allows inplace modification & restart
    [[ ! -f /provisioning.sh ]] && curl -Lo /provisioning.sh "$PROVISIONING_SCRIPT"
    dos2unix /provisioning.sh && \
    chmod +x /provisioning.sh && \
    (set -o pipefail; /provisioning.sh 2>&1 | tee -a /var/log/portal/provisioning.log) && \
    touch /.provisioning_complete && \
    rm -f /.provisioning_failed && \
    echo "Provisioning complete!" | tee -a /var/log/portal/provisioning.log

    if [[ ! -f /.provisioning_complete ]]; then
        touch /.provisioning_failed
        echo "Note: Provisioning encountered issues but instance startup will continue" | tee -a /var/log/portal/provisioning.log
    fi
fi