#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "ray dash"

trap 'ray stop' EXIT

# Check we are actually trying to serve a model
if [[ -z "${VLLM_MODEL:-}" ]]; then
    echo "Refusing to start ${PROC_NAME} (VLLM_MODEL not set)"
    sleep 6
    exit 0
fi

# Wait for provisioning to complete

while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until instance provisioning has completed (/.provisioning present)"
    sleep 10
done

# Launch Ray
cd ${WORKSPACE}

# NO --node-ip-address HERE, AND IT IS NOT AN OVERSIGHT. Ray's own services bind
# public and this launcher cannot stop them; the note is here so the next person
# does not spend a build finding that out again.
#
# base/28-inadvertent-exposure measures it on every QA cell: gcs_server on :6379,
# two raylet ports, two ray::DashboardAgent ports — one answering unauthenticated
# HTTP 200 — plus two python3 agents, all on a public interface. --dashboard-host
# below IS honoured, which is why the dashboard itself is absent from that list, and
# it governs nothing else Ray starts.
#
# `--node-ip-address 127.0.0.1` was tried and is silently UNDONE by Ray. It routes
# the value through services.resolve_ip_for_localhost(), whose docstring is
# "Convert to a remotely reachable IP if the address is localhost or 127.0.0.1" —
# so Ray replaces loopback with the node's real address on purpose, assuming a
# cluster network. Measured on a live cell after passing the flag: Ray logged
# "Local node IP: 172.17.0.2" and the violation set was unchanged. A flag that
# reads as a loopback pin and does nothing is worse than its absence, so it is gone.
#
# NOT reachable from outside: the platform forwards only ports a template maps, and
# none of these are mapped. This is a correctness gap, not an incident. Closing it
# needs either Ray's per-service port flags (--node-manager-port,
# --object-manager-port, --dashboard-agent-listen-port, --min/--max-worker-port) so
# the ports become predictable and declarable, or an exposure-allowlist that can key
# on a PROCESS rather than a port number — the current format is port-keyed and
# Ray's are ephemeral, so today it cannot express them.
ray start ${RAY_ARGS:---head --port 6379 --dashboard-host 127.0.0.1 --dashboard-port 28265} 2>&1

sleep infinity