#!/bin/bash

# Ensure log directories exist and are world-writable so that
# non-root supervisor services can write log files.
mkdir -p /var/log/portal
chmod 1777 /var/log/portal
