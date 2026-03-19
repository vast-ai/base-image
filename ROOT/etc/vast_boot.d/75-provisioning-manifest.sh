#!/bin/bash

# Provision the instance using the declarative provisioner.
# Handles both PROVISIONING_MANIFEST (YAML) and PROVISIONING_SCRIPT (legacy bash scripts).
#
# Resolution order for manifest:
#   1. If PROVISIONING_MANIFEST is set (URL or local path), use it (explicit user override)
#   2. If /provisioning.yaml already exists locally, use it (baked into image or previous download)
#   3. Otherwise, no manifest arg is passed
#
# PROVISIONING_SCRIPT is handled internally by the provisioner (Phase 9).
# The provisioner handles URL downloads, retries, failure actions, and webhooks for both.

MANIFEST_SOURCE=""

# Env var takes priority — lets users override a baked-in manifest
if [[ -n "${PROVISIONING_MANIFEST:-}" ]]; then
    MANIFEST_SOURCE="$PROVISIONING_MANIFEST"
elif [[ -f /provisioning.yaml ]]; then
    MANIFEST_SOURCE="/provisioning.yaml"
fi

# Run provisioner if any provisioning source is configured
if [[ -n "$MANIFEST_SOURCE" || -n "${PROVISIONING_SCRIPT:-}" ]] && [[ ! -f /.provisioning_complete ]]; then
    echo "*****"
    echo "*"
    echo "*"
    if [[ -n "$MANIFEST_SOURCE" && -n "${PROVISIONING_SCRIPT:-}" ]]; then
        echo "* Provisioning instance with manifest from ${MANIFEST_SOURCE} and script from ${PROVISIONING_SCRIPT}"
    elif [[ -n "$MANIFEST_SOURCE" ]]; then
        echo "* Provisioning instance with manifest from ${MANIFEST_SOURCE}"
    else
        echo "* Provisioning instance with script from ${PROVISIONING_SCRIPT}"
    fi
    echo "*"
    echo "* This may take a while.  Some services may not start until this process completes."
    echo "* To change this behavior you can edit or remove the PROVISIONING_MANIFEST / PROVISIONING_SCRIPT"
    echo "* environment variables or the /provisioning.yaml file."
    echo "*"
    echo "*"
    echo "*****"

    if [[ -n "$MANIFEST_SOURCE" ]]; then
        provisioner_args=("$MANIFEST_SOURCE")
    else
        provisioner_args=()
    fi

    if provisioner "${provisioner_args[@]}"; then
        touch /.provisioning_complete
        rm -f /.provisioning_failed
        echo "Provisioning complete!"
    else
        touch /.provisioning_failed
        echo "Note: Provisioning encountered issues but instance startup will continue"
    fi
fi
