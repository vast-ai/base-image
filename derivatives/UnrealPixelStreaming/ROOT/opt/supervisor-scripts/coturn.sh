#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"

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
        --user="${TURN_USERNAME:-user}:${TURN_PASSWORD:-${OPEN_BUTTON_TOKEN:-password}}" \
        -p "${VAST_UDP_PORT_70000:-3478}" \
        -X "${PUBLIC_IPADDR:-localhost}" 2>&1
