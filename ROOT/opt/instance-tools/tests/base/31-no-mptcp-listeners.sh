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

# Positive control: open an MPTCP listener on loopback and require `ss -M` to see it.
# Without it, a kernel whose MPTCP socket diagnostics are unavailable lists nothing,
# and the test would pass having checked nothing.
command -v python3 >/dev/null 2>&1 || test_skip "python3 not available for the MPTCP positive control"
probe_port_file=$(mktemp)
python3 - "$probe_port_file" <<'EOF' 2>/dev/null &
import socket, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 262)  # IPPROTO_MPTCP
s.bind(("127.0.0.1", 0))
s.listen()
with open(sys.argv[1], "w") as f:
    f.write(str(s.getsockname()[1]))
time.sleep(30)
EOF
probe_pid=$!
trap 'kill "$probe_pid" 2>/dev/null; rm -f "$probe_port_file"' EXIT
probe_port=""
for _ in $(seq 1 50); do
    probe_port=$(cat "$probe_port_file" 2>/dev/null)
    [[ -n "$probe_port" ]] && break
    kill -0 "$probe_pid" 2>/dev/null || break
    sleep 0.1
done
[[ -n "$probe_port" ]] || test_skip "cannot open an MPTCP socket here; the image cannot be judged"
ss -lnMH 2>/dev/null | awk '{print $4}' | grep -qx "127.0.0.1:${probe_port}" \
    || test_skip "ss does not list a known MPTCP listener (127.0.0.1:${probe_port}); MPTCP socket diagnostics are unavailable here"

# Syncthing adds its public sync listener after its GUI is up. Read the table too early and
# its half of the check passes with nothing to look at.
syncthing_missing=""
if [[ "${VAST_TCP_PORT_72299:-}" =~ ^[0-9]+$ ]] && service_running syncthing; then
    wait_for_port "$VAST_TCP_PORT_72299" 120 || syncthing_missing="$VAST_TCP_PORT_72299"
fi

offenders=()
while read -r _state _recvq _sendq addr _peer _rest; do
    [[ -z "$addr" || "$addr" == "127.0.0.1:${probe_port}" ]] && continue
    port="${addr##*:}"
    host="${addr%:*}"
    case "$host" in
        127.*|"[::1]"|"::1"|"[::ffff:127."*) echo "  loopback MPTCP listener (allowed): ${addr}"; continue ;;
    esac
    owner=$(listener_owner "$port" || true)
    offenders+=("${addr} (${owner:-owner unknown})")
done < <(ss -lnMH 2>/dev/null)

if (( ${#offenders[@]} )); then
    for o in "${offenders[@]}"; do echo "  non-loopback MPTCP listener: ${o}"; done
    test_fail "${#offenders[@]} MPTCP listener(s) reachable from outside the container; Go services must listen as plain TCP (SetMultipathTCP(false) or GODEBUG=multipathtcp=0)"
fi

[[ -z "$syncthing_missing" ]] \
    || test_skip "no non-loopback MPTCP listeners, but Syncthing's sync listener on ${syncthing_missing} never appeared, so it was not judged"
test_pass "no non-loopback MPTCP listeners"
