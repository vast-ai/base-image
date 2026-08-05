#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "oobabooga"

. /venv/main/bin/activate

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

export GIT_CONFIG_GLOBAL=/tmp/temporary-git-config
git config --file $GIT_CONFIG_GLOBAL --add safe.directory '*'

# Bind the loopback ports UNCONDITIONALLY (additive, not a replaceable default) so
# a launch template cannot drop them and re-expose the service. server.py / the
# API bind 127.0.0.1 unless --listen is passed; we deliberately never pass it.
# OOBABOOGA_ARGS may ADD extra args (incl. overriding the ports, which argparse
# resolves last-wins), but injecting a bare `--listen` would flip both
# Caddy-fronted ports to 0.0.0.0 — refuse to start in that case (ADR 0004).
if [[ " ${OOBABOOGA_ARGS:-} " =~ (^|[[:space:]])--listen([[:space:]]|$) ]]; then
    echo "$PROC_NAME refusing to start: a bare --listen in OOBABOOGA_ARGS would bind 0.0.0.0 on the Caddy-fronted ports (ADR 0004). Remove it; the app already binds loopback behind Caddy."
    exit 1
fi

# Launch Oobabooga
cd ${DATA_DIRECTORY}text-generation-webui
LD_PRELOAD=libtcmalloc_minimal.so.4 \
        pty python server.py \
        --listen-port 17860 --api --api-port 15000 ${OOBABOOGA_ARGS:-} 2>&1
