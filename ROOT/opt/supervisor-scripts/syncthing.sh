#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_serverless.sh"
. "${utils}/exit_portal.sh" "syncthing"

# Keep the per-machine settings out of /home/user in case of volume syncing /home
export STCONFDIR=${STCONFDIR:-/opt/syncthing/config}
export STDATADIR=${STDATADIR:-/opt/syncthing/data}

GUI_ADDR="127.0.0.1:18384"
API_KEY=${OPEN_BUTTON_TOKEN:-$(openssl rand -hex 16)}
CLI="/opt/syncthing/syncthing cli --gui-address=${GUI_ADDR} --gui-apikey=${API_KEY}"

run_with_retry() {
    local max_attempts=${MAX_RETRY:-30}
    local attempt=0
    until "$@"; do
        attempt=$((attempt + 1))
        if [[ $attempt -ge $max_attempts ]]; then
            echo "Command failed after ${max_attempts} attempts: $*"
            return 1
        fi
        sleep 1
    done
}

# Remove stale lock files in case a previous instance was force-killed
find "${STCONFDIR}" "${STDATADIR}" -name "LOCK" -delete 2>/dev/null

# Only generate config/certs on first run
if [[ ! -f "${STCONFDIR}/config.xml" ]]; then
    /opt/syncthing/syncthing generate --config="${STCONFDIR}" --data="${STDATADIR}"
    # Apply initial configuration
    sed -i 's|<listenAddress>default</listenAddress>|<listenAddress>dynamic+https://relays.syncthing.net/endpoint</listenAddress>|' "${STCONFDIR}/config.xml"
    sed -i 's/<natEnabled>true<\/natEnabled>/<natEnabled>false<\/natEnabled>/' "${STCONFDIR}/config.xml"
fi

/opt/syncthing/syncthing serve \
    --no-restart \
    --no-browser \
    --gui-address="${GUI_ADDR}" \
    --gui-apikey="${API_KEY}" \
    --no-upgrade 2>&1 &
syncthing_pid=$!

# Wait for the GUI to become available
if ! run_with_retry curl --output /dev/null --silent --head --fail "http://${GUI_ADDR}"; then
    echo "Syncthing failed to start"
    exit 1
fi

# Apply runtime configuration (idempotent set operations)
run_with_retry $CLI config gui insecure-admin-access set true
run_with_retry $CLI config gui insecure-skip-host-check set true
# Add TCP listener for the dynamic port (relay address is set in config.xml).
#
# RECONCILE, don't just add. Three defects lived here, all silent:
#
#   1. `tcp://0.0.0.0:${VAST_TCP_PORT_72299}` with the variable UNSET — true on
#      any template that does not map port 72299 — is not an error. It is a valid
#      address with an empty port, and syncthing resolves that to its own default:
#          config.xml:  <listenAddress>tcp://0.0.0.0:</listenAddress>
#          log:         TCP listener starting (address="[::]:22000")
#      22000 is published by nothing, so inbound direct connections are impossible
#      and sync silently degrades to relay-only — slow and rate-limited, i.e. the
#      one thing syncthing is here to provide never worked.
#
#   2. The old guard `grep -qF "$LISTEN_ADDR"` substring-COLLIDES: when the value
#      was `tcp://0.0.0.0:` it is a prefix of every well-formed
#      `tcp://0.0.0.0:NNNN`, so a correct address already present made the guard
#      skip, and vice versa.
#
#   3. config.xml lives on overlayfs, which survives stop/start (only a destroy
#      clears it). A malformed entry written by an earlier boot therefore outlives
#      the fix unless it is actively removed — so this reconciles the list to
#      exactly what it should be rather than only ever appending.
#
# Gated by linter rule L068 (ADR 0028).
_sync_listeners=$(run_with_retry $CLI config options raw-listen-addresses list) || _sync_listeners=""

# Drop any malformed empty-port entry from an earlier boot, whatever else is set.
while IFS= read -r _addr; do
    [[ -z "$_addr" ]] && continue
    if [[ "$_addr" =~ ^(tcp|quic)://[^]]*:$ ]]; then
        echo "syncthing: removing malformed listen address '${_addr}' (empty port)"
        run_with_retry $CLI config options raw-listen-addresses remove "$_addr" || true
    fi
done <<< "$_sync_listeners"

if [[ "${VAST_TCP_PORT_72299:-}" =~ ^[0-9]+$ ]]; then
    LISTEN_ADDR="tcp://0.0.0.0:${VAST_TCP_PORT_72299}"
    # Exact-line match, not a substring: see defect 2 above.
    if ! grep -qxF "$LISTEN_ADDR" <<< "$_sync_listeners"; then
        run_with_retry $CLI config options raw-listen-addresses add "$LISTEN_ADDR"
    fi
else
    # No mapped port means no reachable TCP listener is possible. Configure none
    # and say so, rather than binding a default that cannot receive anything.
    echo "syncthing: VAST_TCP_PORT_72299 is unset — no direct TCP listener." \
         "Sync will use the configured relay. Map port 72299 in the template to" \
         "enable direct peer connections." >&2
fi

wait $syncthing_pid
