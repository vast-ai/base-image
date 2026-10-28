#!/bin/bash
# Test: supervisord is SERVING — not merely forked.
# Service state checks are in 65-conditional-services.sh (after provisioning).
#
# This is the first test in the suite, and it runs while supervisord is still
# starting: 65-supervisor-launch.sh backgrounds supervisord, then boot moves
# straight on to 70-instance-test.sh, which backgrounds this runner. The two are
# effectively simultaneous.
#
# It used to gate on `pgrep -f supervisord` and then call supervisorctl on the
# next line. Presence is true the instant supervisord forks; the RPC socket every
# other service assertion needs appears later. Measured in the shipped image,
# idle, 16 cores: presence at 1.7ms, socket usable at 383ms. On a QA host still
# provisioning, that gap was wide enough to fail this test 0.09s into the suite
# with `supervisorctl cannot communicate with supervisord (exit 4)`, taking
# 20-portal and 26-caddy-auth down as collateral and blocking a whole promote
# batch — on an image the same suite proved healthy 53 seconds later (ADR 0029).
source "$(dirname "$0")/../lib.sh"

if ! wait_for_supervisor 60; then
    # Only NOW is presence worth asking about, and only to say which failure it
    # is: never started, or started and never served.
    pgrep -f supervisord &>/dev/null \
        || test_fail "supervisord is not running at all"
    test_fail "supervisord is running but its RPC socket never came up after 60s"
fi

# Where the control socket lives (65-supervisor-launch.sh). Decided here by an
# independent probe of the same thing supervisord does (bind, then hard-link), so
# a boot that took the fallback when it did not need to is caught: wherever the
# probe succeeds (every runc host) the layout must be exactly the shipped one.
SOCK=/var/run/supervisor.sock
SOCK_INCLUDE=/etc/supervisor/conf.d/vast-supervisor-socket.conf
SOCK_FALLBACK_DIR=/dev/shm/vast-supervisor
if /usr/bin/python3 - "$(dirname "$SOCK")" <<'PY' 2>/dev/null
import os, socket, sys
a = os.path.join(sys.argv[1], f".t10-probe-{os.getpid()}")
s = socket.socket(socket.AF_UNIX)
try:
    s.bind(a + "-a")
    os.link(a + "-a", a + "-b")
finally:
    s.close()
    for p in (a + "-a", a + "-b"):
        if os.path.lexists(p):
            os.unlink(p)
PY
then
    [[ -S "$SOCK" && ! -L "$SOCK" ]] \
        || test_fail "${SOCK} is not a plain socket although $(dirname "$SOCK") supports one ($(ls -la "$SOCK" 2>&1))"
    [[ ! -e "$SOCK_INCLUDE" ]] \
        || test_fail "${SOCK_INCLUDE} exists although the shipped socket path works"
else
    target=$(readlink "$SOCK")
    [[ -L "$SOCK" && "$target" == "${SOCK_FALLBACK_DIR}/supervisor.sock" ]] \
        || test_fail "$(dirname "$SOCK") cannot hold a linked socket, but ${SOCK} is not a symlink to ${SOCK_FALLBACK_DIR}/supervisor.sock (got '${target}')"
    [[ -S "$target" ]] || test_fail "fallback socket ${target} is not a socket"
    [[ ! -L "$SOCK_FALLBACK_DIR" && "$(stat -c '%u %a' "$SOCK_FALLBACK_DIR")" == "0 700" ]] \
        || test_fail "${SOCK_FALLBACK_DIR} is not a root-owned 0700 directory ($(stat -c '%U %a %F' "$SOCK_FALLBACK_DIR" 2>&1))"
    grep -qx "file=${target}" "$SOCK_INCLUDE" 2>/dev/null \
        || test_fail "${SOCK_INCLUDE} does not point supervisord at ${target}"
    echo "  control socket: ${target} (fallback; ${SOCK} links to it)"
fi

# Clients with no environment at all (cron, a bare `env -i`) reach it on the
# literal serverurl. Resolved on PATH first: external images install supervisor in
# their own venv and expose it through a shim, not in /usr/local/bin.
sc=$(command -v supervisorctl) || test_fail "supervisorctl is not on PATH"
env -i "$sc" status >/dev/null 2>&1; rc=$?
(( rc == 0 || rc == 3 )) || test_fail "env -i ${sc} status failed (exit ${rc})"

test_pass "supervisord running and serving on its RPC socket"
