#!/bin/bash

if [[ "${update_portal}" = "true" ]]; then
    update-portal ${PORTAL_VERSION:+-v $PORTAL_VERSION}
fi
