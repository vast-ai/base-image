#!/bin/bash
# 14-instance-allocation.sh — record the RAM and disk allocation where the sandbox hides them.
#
# Under gVisor the container sees only its hard memory limit (not the guarantee) and, with a host-served root, the host's filesystem size. The portal then needs
# the allocation from outside: bin/instance-allocation reads it once from the instance API
# and writes /run/vast-allocation.json (ADR 0055). Under runc both figures are visible locally,
# so nothing runs and nothing changes.
#
# A runtime check, not a capability probe: nothing inside the container can tell whether the
# visible memory limit is the guarantee. In the background, so a host that
# cannot reach the API does not delay the boot.
#
# SOURCED by boot_default.sh: never `exit` here, it would stop the container.

if /opt/instance-tools/bin/is-gvisor; then
    ( /opt/instance-tools/bin/instance-allocation 2>&1 | sed -u 's/^/[allocation] /' ) &
fi
