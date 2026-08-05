#!/bin/bash

# Fresh-instance first-login credential for Unsloth Studio.
#
# On first start the studio seeds its admin account from a bootstrap-password
# file that lives beside its auth DB (studio/backend/auth/storage.py). When that
# file is absent it generates a RANDOM diceware passphrase, writes it to disk,
# and seeds the admin with must_change_password=True. It will auto-fill that
# password into the login page ONLY for a direct-loopback, same-origin request;
# behind our Caddy proxy the request is proxied, injection is suppressed, and
# the user is forced to SSH in and read the passphrase off disk before they can
# even log in.
#
# Pre-seed a known, documented first-login password ("password") on a FRESH
# instance only (no auth DB yet). The studio then seeds the admin with this
# password AND must_change_password=True (the bootstrap-file path always sets
# it), so the user logs in with the documented credential and is immediately
# forced to change it. This is safe because the UI already sits behind Caddy
# token auth — the weak default only ever exists pre-rotation, on the loopback
# side of the proxy. See ADR 0016.
#
# Runs AFTER 38-unsloth-symlinks.sh so /root/.unsloth already points at the
# runtime location (overlay or $WORKSPACE volume). Guarded on auth.db absence,
# so it never clobbers an instance whose user has already set a password
# (credentials persist across stop/start).

auth_dir="/root/.unsloth/studio/auth"

if [[ ! -e "${auth_dir}/auth.db" ]]; then
    mkdir -p "${auth_dir}"
    printf 'password' > "${auth_dir}/.bootstrap_password"
    chmod 600 "${auth_dir}/.bootstrap_password"
fi
