#!/bin/bash

# Remove stale /etc/ld.so.preload from a previous session.
# kde.sh writes libvglfaker.so into this file after the desktop starts
# and cleans it up on exit, but an unclean shutdown can leave it behind.
rm -f /etc/ld.so.preload
