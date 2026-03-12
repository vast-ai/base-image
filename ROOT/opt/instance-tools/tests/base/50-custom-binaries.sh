#!/bin/bash
# Test: custom binaries and tools installed in the base image.
source "$(dirname "$0")/../lib.sh"

# Always-expected commands
for cmd in uv supervisorctl jq git-lfs; do
    assert_command_exists "$cmd"
done

# Skip-if-absent binaries — check what IS present
declare -A OPTIONAL_BINS=(
    [caddy]=caddy
    [cloudflared]=cloudflared
    [log-tee]=log-tee
    [unbuffer]=unbuffer
    [env-hash]=env-hash
    [provisioner]=provisioner
)

for label in "${!OPTIONAL_BINS[@]}"; do
    cmd="${OPTIONAL_BINS[$label]}"
    if command -v "$cmd" &>/dev/null; then
        echo "  present: $label"
    else
        echo "  absent (ok): $label"
    fi
done

# Syncthing (non-standard path)
if [[ -x /opt/syncthing/syncthing ]]; then
    echo "  present: syncthing"
else
    echo "  absent (ok): syncthing"
fi

# nvm + node (skip-if-absent)
if [[ -d /opt/nvm ]]; then
    echo "  present: nvm"
    export NVM_DIR=/opt/nvm
    # shellcheck disable=SC1091
    [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" 2>/dev/null
    if command -v node &>/dev/null; then
        echo "  node: $(node --version 2>&1)"
    else
        echo "  WARN: nvm present but node not available"
    fi
else
    echo "  absent (ok): nvm"
fi

test_pass "custom binaries verified"
