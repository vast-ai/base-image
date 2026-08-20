if [ -z "$1" ]; then
    echo "Error: No application name provided"
    exit 1
fi

search_term="$1"

# Portal config is not relevant in serverless mode — skip the check entirely
if [[ "${SERVERLESS,,}" = "true" ]]; then
    return 0 2>/dev/null || true
fi

# User can configure startup by removing the reference in /etc/portal.yaml - So wait for that file and check it
#
# Wait for CONTENT, not existence. The release condition used to be `-f`, but the
# decision below is a grep — so a reader that won the file-exists race and lost
# the file-written race greps an empty file, finds nothing, and takes the
# self-skip path. That is not a retry: `sleep 6; exit 0` under
# autorestart=unexpected + exitcodes=0 means supervisord treats it as intentional
# and never restarts, so the service is gone for the life of the instance — and
# the portal hides it, because a skip marker reads as "not configured" rather
# than "failed". Measured window in the writer: ~0.5ms idle, 90ms under CPU
# throttling, against this 1s poll.
#
# The writer is atomic now (tempfile + os.replace), so this is belt and braces
# against a stale zero-byte file from an older image or a crashed configurator.
# Bounded, because a wait with no budget on a file that may never arrive turns a
# missing config into a hung instance with nothing to point at.
_portal_wait=0
while :; do
    _portal_yaml="$(realpath -q /etc/portal.yaml 2>/dev/null)"
    # -s, not -f: non-empty. A zero-byte file cannot answer the question below.
    [ -n "$_portal_yaml" ] && [ -s "$_portal_yaml" ] && break
    if [ "$_portal_wait" -ge 120 ]; then
        echo "ERROR: /etc/portal.yaml still absent or empty after ${_portal_wait}s;" \
             "refusing to decide ${PROC_NAME} startup from an unreadable config"
        exit 1
    fi
    echo "Waiting for /etc/portal.yaml before starting ${PROC_NAME}..."
    sleep 1
    _portal_wait=$((_portal_wait + 1))
done

# Check for $search_term in the portal config
if ! grep -qiE "^[^#].*${search_term}" /etc/portal.yaml; then
    echo "Skipping ${PROC_NAME} startup (not in /etc/portal.yaml)"
    if [[ -n "${PROC_NAME}" ]]; then
        # Sticky, like /tmp itself. syncthing.conf runs `user=user`, so whichever
        # service reaches here first owns the directory: if that was a root
        # service (0755 root:root), syncthing's marker write fails with
        # "Permission denied" and the portal shows it as a dead process instead
        # of a correctly-skipped one. Ownership by arrival order is not a design.
        # -m sets the mode AT CREATION. `mkdir` then `chmod` is two syscalls —
        # the same "atomic lock, separate marker" shape fixed in the boot stages
        # above — and between them a non-root service's write still fails. The
        # chmod stays only as the upgrade path for a 0755 directory left by an
        # older image, where it is a no-op for a non-owner anyway.
        mkdir -m 1777 -p /tmp/supervisor-skip 2>/dev/null
        chmod 1777 /tmp/supervisor-skip 2>/dev/null || true
        echo "${search_term}" > "/tmp/supervisor-skip/${PROC_NAME}"
    fi
    sleep 6
    exit 0
fi
# Clear skip marker if process is configured
[[ -n "${PROC_NAME}" ]] && rm -f "/tmp/supervisor-skip/${PROC_NAME}"