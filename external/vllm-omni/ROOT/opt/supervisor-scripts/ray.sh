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

# --node-ip-address is the bind interface for Ray's OWN services, and without it
# they land on 0.0.0.0. --dashboard-host already pinned the dashboard; it does not
# touch the rest, and the rest is most of the surface. Measured by
# base/28-inadvertent-exposure on a live QA cell: gcs_server on :6379, two raylet
# ports, two ray::DashboardAgent ports — one of them answering unauthenticated
# HTTP 200 — all bound public. They are not REACHABLE, because the platform
# forwards only ports a template maps and none of these are mapped, so this is
# correctness rather than an incident. It is still the wrong default to ship:
# the repo's rule is that a service binds loopback and is reached through Caddy,
# and the bind is stated explicitly in the launch rather than left implicit.
#
# Safe for the multi-node case because it cannot reach it: RAY_ARGS replaces this
# default wholesale, and vllm.sh only starts a local head when RAY_ADDRESS is unset
# or already 127.0.0.1 — anyone joining a remote cluster is on the other branch of
# that predicate and supplies their own RAY_ARGS.
ray start ${RAY_ARGS:---head --node-ip-address 127.0.0.1 --port 6379 --dashboard-host 127.0.0.1 --dashboard-port 28265} 2>&1

sleep infinity
