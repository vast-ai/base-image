#!/bin/bash
# Test: no MPTCP listener is reachable from outside the container.
#
# Go 1.24+ opens MPTCP listeners by default, so every Go service we ship (Caddy,
# Syncthing, anything an app brings) listens as MPTCP unless told not to. The
# published attack on CVE-2026-98432, a host-kernel IPv4 TCP handshake
# use-after-free, needs an MPTCP listener. The kernel is the fix; this keeps our
# images from offering the listener type the known attack path uses.
#
# Loopback listeners are allowed: no remote peer can reach them. Anything else
# (wildcard or a specific non-loopback address) fails.
#
# The answer is read from the kernel's own socket table (`ss -M`), not from
# build flags or environment, so it holds for any way a service is started.
source "$(dirname "$0")/../lib.sh"

ss -lnMH >/dev/null 2>&1 || test_skip "ss cannot list MPTCP sockets here (iproute2 without --mptcp)"

# With MPTCP off in this network namespace nothing can listen as MPTCP, so a
# pass would say nothing about the image. Skip loudly rather than pass silently.
enabled=$(cat /proc/sys/net/mptcp/enabled 2>/dev/null || echo 0)
[[ "$enabled" == "1" ]] || test_skip "MPTCP is disabled in this network namespace (net.mptcp.enabled=${enabled}); the image cannot be judged here"

offenders=()
while read -r _state _recvq _sendq addr _peer _rest; do
    [[ -z "$addr" ]] && continue
    port="${addr##*:}"
    host="${addr%:*}"
    case "$host" in
        127.*|"[::1]"|"::1") echo "  loopback MPTCP listener (allowed): ${addr}"; continue ;;
    esac
    owner=$(listener_owner "$port" || true)
    offenders+=("${addr} (${owner:-owner unknown})")
done < <(ss -lnMH 2>/dev/null)

if (( ${#offenders[@]} )); then
    for o in "${offenders[@]}"; do echo "  non-loopback MPTCP listener: ${o}"; done
    test_fail "${#offenders[@]} MPTCP listener(s) reachable from outside the container; Go services must listen as plain TCP (SetMultipathTCP(false) or GODEBUG=multipathtcp=0)"
fi

test_pass "no non-loopback MPTCP listeners"
