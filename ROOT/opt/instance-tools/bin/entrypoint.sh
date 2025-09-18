#!/bin/bash

# Use the entrypoint as an early bootstrapping mechanism for a customizable boot script. 
# We should always provide the sensible default, but allow override to give full control.

default_boot_script=/opt/instance-tools/bin/boot_default.sh
custom_boot_script=/opt/instance-tools/bin/boot_custom.sh

if [[ -n "${BOOT_SCRIPT:-}" && ! -f "$custom_boot_script" ]]; then
    if curl -L -o /tmp/boot_custom.sh "$BOOT_SCRIPT"; then
        mv /tmp/boot_custom.sh "$custom_boot_script"
        chmod +x "$custom_boot_script"
        dos2unix "$custom_boot_script"
    fi
fi

if [[ -f "$custom_boot_script" ]]; then
    exec "$custom_boot_script" "$@"
else
    exec "$default_boot_script" "$@"
fi