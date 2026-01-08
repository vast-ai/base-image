#!/bin/bash

# Source any scripts that should only run on first boot

if [[ ! -f /.first_boot_complete ]]; then
    for script in /etc/vast_boot.d/first_boot/*.sh; do
        [[ -f "$script" ]] && [[ -r "$script" ]] && . "$script"
    done

    touch /.first_boot_complete
fi
