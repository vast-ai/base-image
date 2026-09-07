#!/bin/bash
# Test: the base still provides the three torch venvs, and the helper that shares them.
# TEST_TIMEOUT=600
source "$(dirname "$0")/../lib.sh"

# THE BASE IS THE THING THE APP LAYER ASSUMES, so it is worth asserting on its own.
#
# aio-studio's eight apps do not each install torch. `create_app_venv <app> <base>`
# builds a per-app venv whose site-packages shadows one of the venvs BELOW, reached
# through a `_base.pth` line, copying only torch-ecosystem dist-info so uv treats torch
# as already installed. That means every app inherits whatever this base provides, and a
# base that quietly lost a venv — or was rebuilt on the wrong multi-torch family —
# produces app venvs that resolve nothing.
#
# This file ships in ROOT_BASE, so it runs on the BASE image (which is what a base QA
# cell rents) and again on the app image built from it. That is deliberate: the same
# contract should hold at both layers, and the app image is where it could be broken by
# a later step.

# The base's own stated contract (Dockerfile.base): main is the newest of the multi
# family, plus the two pinned lines the apps need.
declare -A WANT=(
    [main]=""            # resolved below — the newest member moves, pinning it here
                         # would turn a routine base bump into a red cell
    [torch-2.9.1]=2.9.1
    [torch-2.7.1]=2.7.1
)

echo "  -- base venvs --"
for v in "${!WANT[@]}"; do
    py="/venv/${v}/bin/python"
    if [[ ! -x "$py" ]]; then
        fail_later "venv-${v}" "/venv/${v} is missing — this base is not the multi-210-291-271 family the apps need"
        continue
    fi
    got=$("$py" -c 'import torch; print(torch.__version__)' 2>/dev/null | cut -d+ -f1)
    if [[ -z "$got" ]]; then
        fail_later "torch-${v}" "/venv/${v} exists but cannot import torch"
        continue
    fi
    want="${WANT[$v]}"
    if [[ -n "$want" && "$got" != "$want" ]]; then
        fail_later "torch-${v}" "/venv/${v} has torch ${got}, expected ${want}"
        continue
    fi
    echo "  /venv/${v}: torch ${got}"
done

# flash-attn is installed into torch-2.9.1 in the base precisely so the 2.9.1 children
# do not each compile it. amd64-only by construction (the wheel is linux_x86_64), so
# this is reported rather than asserted on other arches.
echo ""
echo "  -- shared heavy packages --"
if [[ "$(dpkg --print-architecture)" == "amd64" ]]; then
    if /venv/torch-2.9.1/bin/python -c 'import flash_attn' 2>/dev/null; then
        echo "  torch-2.9.1: flash_attn importable"
    else
        fail_later "flash-attn" "/venv/torch-2.9.1 cannot import flash_attn — the shared wheel did not land, so every 2.9.1 child pays for it or fails"
    fi
else
    echo "  skip: flash_attn (wheel is amd64-only by construction)"
fi

# The helper itself. Without it the app build fails immediately, but a base QA cell is
# the cheapest place to notice it is gone.
echo ""
echo "  -- create_app_venv --"
if [[ -x /usr/local/bin/create_app_venv ]]; then
    echo "  create_app_venv: present and executable"
else
    fail_later "helper" "/usr/local/bin/create_app_venv is missing or not executable — no app venv can be built on this base"
fi

report_failures
test_pass "base torch venvs and the app-venv helper are intact"
