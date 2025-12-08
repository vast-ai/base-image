#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"


. /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
nix-daemon 2>&1

