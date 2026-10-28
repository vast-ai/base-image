#!/bin/bash
# 65-supervisor-launch.sh — choose where supervisord's control socket lives, then
# launch supervisord in the background.
#
# supervisord publishes its socket by binding a temporary name and hard-linking it
# to the configured path. On some root filesystems (gVisor with a host-served root)
# link() on a unix socket fails with EOPNOTSUPP, and supervisord then retries
# forever and starts no program. So every boot:
#
#   * start from the shipped layout: remove /var/run/supervisor.sock (a socket or a
#     symlink) and the include a previous boot may have generated;
#   * probe that directory the way supervisord will use it (bind, then link). If it
#     works nothing changes: a plain socket at the usual path, the shipped config;
#   * otherwise create a fresh root-only directory under /dev/shm, probe that, write
#     an include that overrides ONLY `[unix_http_server] file=`, and symlink the
#     usual path to the socket there so every client keeps working unchanged.
#     `serverurl` stays literal in supervisord.conf;
#   * if neither works, say so loudly and do NOT launch supervisord.
#     95-supervisor-wait.sh then keeps the boot alive so SSH and Jupyter stay up.
#
# SOURCED by boot_default.sh: never `exit` here, it would stop the container.

# 0 when a unix socket can be bound in <dir> and hard-linked within it, which is
# exactly what supervisord does with its control socket. Cleans up after itself.
_vast_sock_probe() {
    /usr/bin/python3 - "$1" <<'PY' 2>/dev/null
import os, socket, sys
base = os.path.join(sys.argv[1], f".vast-sock-probe.{os.getpid()}")
tmp, dst = base + ".tmp", base + ".sock"
s = socket.socket(socket.AF_UNIX)
try:
    s.bind(tmp)
    os.link(tmp, dst)
finally:
    s.close()
    for p in (tmp, dst):
        try:
            os.unlink(p)
        except OSError:
            pass
PY
}

# Decide the socket location and lay it out. Echo the socket path supervisord will
# bind and return 0, or return 1 when no location works. Diagnostics go to stderr.
#   <sock>     the path supervisord.conf names (and clients use)
#   <include>  the generated include under /etc/supervisor/conf.d/
#   <shm_dir>  the fallback directory under /dev/shm
#   [uid]      the owner the fallback directory must have (default 0: the boot runs as root)
_vast_supervisor_socket() {
    local sock="$1" include="$2" shm_dir="$3" uid="${4:-0}" fallback
    rm -f "$sock" "$include"

    if _vast_sock_probe "$(dirname "$sock")"; then
        echo "$sock"
        return 0
    fi
    echo "[supervisor-launch] $(dirname "$sock") cannot hold a linked unix socket; trying ${shm_dir}" >&2

    # mkdir, not -p: the directory must be one this boot created. A pre-existing
    # path (directory or symlink) is refused rather than reused.
    if ! mkdir -m 0700 "$shm_dir" 2>/dev/null; then
        echo "[supervisor-launch] refusing ${shm_dir}: it already exists or cannot be created" >&2
        return 1
    fi
    if [[ -L "$shm_dir" || ! -d "$shm_dir" ]] \
        || [[ "$(stat -c '%u %a' "$shm_dir" 2>/dev/null)" != "${uid} 700" ]]; then
        echo "[supervisor-launch] refusing ${shm_dir}: not a directory owned by uid ${uid} with mode 0700" >&2
        return 1
    fi
    if ! _vast_sock_probe "$shm_dir"; then
        echo "[supervisor-launch] ${shm_dir} cannot hold a linked unix socket either" >&2
        rmdir "$shm_dir" 2>/dev/null
        return 1
    fi

    fallback="${shm_dir}/supervisor.sock"
    if ! printf '; Generated at boot by 65-supervisor-launch.sh; removed at the start of every boot.\n[unix_http_server]\nfile=%s\n' \
            "$fallback" > "$include"; then
        echo "[supervisor-launch] could not write ${include}" >&2
        return 1
    fi
    ln -s "$fallback" "$sock" \
        || echo "[supervisor-launch] WARNING: could not link ${sock} -> ${fallback}; clients on the usual path will not reach supervisord" >&2
    echo "$fallback"
}

# Choose the socket, then launch supervisord, or leave it unlaunched (and
# supervisord_pid unset) when no location works. Sets supervisord_pid in the boot
# shell for 95-supervisor-wait.sh.
_vast_supervisor_launch() {
    local sock="$1" include="$2" shm_dir="$3" uid="${4:-0}" chosen
    unset supervisord_pid
    if ! chosen=$(_vast_supervisor_socket "$sock" "$include" "$shm_dir" "$uid"); then
        echo "[supervisor-launch] ERROR: no location can hold supervisord's control socket."
        echo "[supervisor-launch] ERROR: supervisord is NOT started: no managed service (portal, Caddy, apps) will run."
        echo "[supervisor-launch] ERROR: the container stays up so SSH and Jupyter remain available for debugging."
        return 1
    fi
    if [[ "$chosen" == "$sock" ]]; then
        echo "[supervisor-launch] control socket: ${sock}"
    else
        echo "[supervisor-launch] control socket: ${chosen} (fallback; ${sock} links to it, ${include} sets it)"
    fi
    # Background it so provisioning can be monitored in the Instance Portal.
    supervisord \
        -n \
        -u root \
        -c /etc/supervisor/supervisord.conf &
    supervisord_pid=$!
}

# Skip the boot action when sourced lib-only (the test loads functions this way).
[[ -n "${_VAST_SUPERVISOR_LAUNCH_LIB_ONLY:-}" ]] && return 0

# Indicator for supervisor scripts to prevent launch during provisioning if necessary (if [[ -f /.provisioning ]] ...)
touch /.provisioning

_vast_supervisor_launch /var/run/supervisor.sock \
    /etc/supervisor/conf.d/vast-supervisor-socket.conf \
    /dev/shm/vast-supervisor
