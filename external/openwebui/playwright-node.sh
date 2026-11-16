#!/bin/bash
# Build-time only: bring the Node that Playwright's driver bundles to the trust floor (ADR 0058).
#
# Node compiles its roots in, so its trust only moves with a Node release, and Playwright ships
# the Node of its own release date inside the Python package. Replace that binary with the
# first fixed release on the SAME major line (lib/node-trust-floor.txt), verified by hash, and
# prove the driver still starts on it. A different major is never substituted.
set -euo pipefail

PY="${VAST_ENGINE_PYTHON:?VAST_ENGINE_PYTHON is not set}"
FLOORS=/opt/instance-tools/lib/node-trust-floor.txt

# "Not installed" is nothing to do; installed but unusable is a build failure.
if ! "$PY" -c 'import importlib.util, sys; sys.exit(importlib.util.find_spec("playwright") is None)'; then
    echo "playwright-node: Playwright is not installed in this image; nothing to do"
    exit 0
fi
driver=$("$PY" -c 'import os, playwright; print(os.path.join(os.path.dirname(playwright.__file__), "driver"))')
[[ -x "$driver/node" ]] || { echo "playwright-node: Playwright is installed but ${driver}/node is missing" >&2; exit 1; }

older() { [[ "$1" != "$2" && "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" == "$1" ]]; }
have=$("$driver/node" -p 'process.versions.node')
floor=$(awk -v m="${have%%.*}" '$1 == m {print $2}' "$FLOORS")
[[ -n "$floor" ]] || { echo "playwright-node: no fixed release listed for Node ${have%%.*}.x (driver has ${have})" >&2; exit 1; }

if older "$have" "$floor"; then
    case "$(dpkg --print-architecture)" in
        amd64) arch=x64 ;;
        arm64) arch=arm64 ;;
        *) echo "playwright-node: unsupported architecture" >&2; exit 1 ;;
    esac
    # Published by nodejs.org in SHASUMS256.txt for each release. The hash, not the TLS
    # connection, is what makes the download trustworthy. Add a line when a floor moves.
    case "${floor}-${arch}" in
        22.23.4-x64)   sha=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce ;;
        22.23.4-arm64) sha=6287aa46b6c375d816e63ae66d024fbbfd70404825aabdfdf898a970871d2f4b ;;
        24.22.1-x64)   sha=6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6 ;;
        24.22.1-arm64) sha=dbabf7a1cbf947e6059ffad2c27b80041db97dea1e553b62afdee7f13fca336e ;;
        *) echo "playwright-node: no pinned hash for Node ${floor} ${arch}" >&2; exit 1 ;;
    esac
    name="node-v${floor}-linux-${arch}"
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
    curl -fsSL --retry 5 --retry-all-errors -o "$work/node.tar.xz" "https://nodejs.org/dist/v${floor}/${name}.tar.xz"
    echo "${sha}  $work/node.tar.xz" | sha256sum -c --quiet -
    mkdir -p /opt/playwright-node/bin
    tar -xJf "$work/node.tar.xz" -C "$work" "${name}/bin/node" "${name}/LICENSE"
    install -m 0755 "$work/${name}/bin/node" /opt/playwright-node/bin/node
    install -m 0644 "$work/${name}/LICENSE" /opt/playwright-node/LICENSE
    ln -sf /opt/playwright-node/bin/node "$driver/node"
    echo "playwright-node: driver Node ${have} -> $("$driver/node" -p 'process.versions.node')"
fi

now=$("$driver/node" -p 'process.versions.node')
[[ "${now%%.*}" == "${have%%.*}" ]] && ! older "$now" "$floor" \
    || { echo "playwright-node: driver Node is ${now}; need ${have%%.*}.x at or above ${floor}" >&2; exit 1; }
# The driver is a Node program the Python package talks to over a pipe: start it and stop it.
"$PY" -c 'from playwright.sync_api import sync_playwright
p = sync_playwright().start()
p.stop()
print("playwright-node: driver starts on its Node")'
