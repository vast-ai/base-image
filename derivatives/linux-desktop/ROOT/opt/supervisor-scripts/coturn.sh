#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

[[ -n $TURN_SERVER ]] && echo "Refusing to start ${PROC_NAME} (External TURN_SERVER configured)" && exit

turnserver \
        -n \
        -a \
        --log-file=stdout \
        --lt-cred-mech \
        --fingerprint \
        --no-stun \
        --no-multicast-peers \
        --no-cli \
        --no-tlsv1 \
        --no-tlsv1_1 \
        --realm="vast.ai" \
        --user="${TURN_USERNAME:-turnuser}:${TURN_PASSWORD:-${OPEN_BUTTON_TOKEN:-password}}" \
        -p "${VAST_UDP_PORT_73478:-${VAST_TCP_PORT_73478:-73478}}" \
        -X "${PUBLIC_IPADDR:-localhost}"
