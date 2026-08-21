#!/bin/bash

# Ensure log directories exist and are world-writable so that
# non-root supervisor services can write log files.
mkdir -p /var/log/portal
chmod 1777 /var/log/portal

# log-tee derives a CLEAN copy at /var/log/<name>.log alongside the coloured
# /var/log/portal/<name>.log. The portal dir is handled above, but /var/log
# itself is 775 root:syslog and `user` is in group root, NOT syslog — so a
# service that drops privileges cannot create its clean log, and log-tee's
# attempt fails silently. syncthing.conf is the one base unit with `user=`, and
# its clean log has never existed: base/70-logging has WARNed about it on every
# QA cell of every run, which is how it stayed invisible.
#
# Pre-create as root and hand ownership over. Driven off conf.d rather than a
# hardcoded name, so a future privilege-dropping service is covered too.
for _conf in /etc/supervisor/conf.d/*.conf; do
    [[ -f "$_conf" ]] || continue
    _svc_user=$(grep -oP '^\s*user=\K\S+' "$_conf" 2>/dev/null)
    [[ -n "$_svc_user" ]] || continue
    _svc_name=$(basename "$_conf" .conf)
    touch "/var/log/${_svc_name}.log" 2>/dev/null || continue
    chown "$_svc_user" "/var/log/${_svc_name}.log" 2>/dev/null || true
done
