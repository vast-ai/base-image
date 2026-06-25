#!/usr/bin/env bash
# Runtime bind-address smoke gate — ADR 0002 binding condition 1.
#
# This is the REAL safety gate the static L051 check defers to: a static linter
# cannot see where a process actually binds, and both prior loopback-exposure
# incidents were apps binding 0.0.0.0 instead of 127.0.0.1. So we boot the image
# and assert nothing reachable is bound public without Caddy in front.
#
# What it does:
#   1. reads the image's EXPOSE'd ports from its Dockerfile
#   2. boots the image, simulating the platform's port mapping by exporting
#      VAST_TCP_PORT_<ext> for each EXPOSE'd port (so Caddy actually stands up its
#      proxy sites — see caddy_config_manager.py:217)
#   3. waits for boot to settle, then dumps `ss -ltnp` and the RENDERED runtime
#      PORTAL_CONFIG (from /etc/portal.yaml, converted in-container where pyyaml
#      lives — satisfies ADR 0002 condition 2: validate the rendered config, not
#      the at-rest baked string)
#   4. feeds both to `imagegen bindcheck`, which is the verdict
#
# Usage:
#   tools/imagegen/smoke/bind-check.sh <image-tag> <image-name> [boot-wait-seconds]
# Example:
#   tools/imagegen/smoke/bind-check.sh vastai/vllm:latest vllm 60
#
# Exit: 0 = pass, 1 = bind violation (FAIL the build), 2 = harness error.
set -euo pipefail

IMAGE_TAG="${1:?usage: bind-check.sh <image-tag> <image-name> [boot-wait]}"
IMAGE_NAME="${2:?usage: bind-check.sh <image-tag> <image-name> [boot-wait]}"
BOOT_WAIT="${3:-60}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGEGEN_DIR="$(cd "${HERE}/.." && pwd)"
REPO_ROOT="$(cd "${IMAGEGEN_DIR}/../.." && pwd)"
DOCKERFILE_PATH=""
for cand in \
    "${REPO_ROOT}/external/${IMAGE_NAME}/Dockerfile" \
    "${REPO_ROOT}/derivatives/${IMAGE_NAME}/Dockerfile" \
    "${REPO_ROOT}/derivatives/pytorch/derivatives/${IMAGE_NAME}/Dockerfile"; do
    [[ -f "$cand" ]] && DOCKERFILE_PATH="$cand" && break
done
[[ -n "$DOCKERFILE_PATH" ]] || { echo "✗ could not locate Dockerfile for ${IMAGE_NAME}"; exit 2; }

# `imagegen` runner: prefer an installed entrypoint, else module via uv.
run_imagegen() {
    if command -v imagegen >/dev/null 2>&1; then
        imagegen "$@"
    else
        ( cd "$IMAGEGEN_DIR" && PYTHONPATH=. uv run --no-project python -m imagegen.cli "$@" )
    fi
}

# 1. EXPOSE'd ports (the externally-mapped set in this no-template run).
EXPOSED="$(grep -hiE '^\s*EXPOSE\s' "$DOCKERFILE_PATH" | sed -E 's/^\s*EXPOSE\s+//I; s#/[a-z]+##g' | tr '\n' ' ' | tr -s ' ')"
echo "▸ ${IMAGE_NAME}: EXPOSE'd ports = [${EXPOSED:-none}]"

# 2. boot, simulating the platform's port mapping so Caddy stands up its sites.
ENV_ARGS=()
for p in $EXPOSED; do
    [[ "$p" =~ ^[0-9]+$ ]] && ENV_ARGS+=(-e "VAST_TCP_PORT_${p}=${p}")
done
# Jupyter/SSH are auto-opened by the platform; mirror that so Caddy fronts 8080.
ENV_ARGS+=(-e "VAST_TCP_PORT_8080=8080" -e "OPEN_BUTTON_PORT=1111")

CID="$(docker run -d --rm "${ENV_ARGS[@]}" "$IMAGE_TAG")"
cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "▸ booted ${CID:0:12}; waiting ${BOOT_WAIT}s for services to settle…"
sleep "$BOOT_WAIT"

# tooling: ss may be absent in slim images; iproute2 provides it.
docker exec "$CID" sh -c 'command -v ss >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq iproute2) >/dev/null 2>&1 || true'

SS_DUMP="$(docker exec "$CID" ss -ltnp 2>/dev/null || true)"
[[ -n "$SS_DUMP" ]] || { echo "✗ could not capture `ss -ltnp` from the container"; exit 2; }

# 3. rendered runtime PORTAL_CONFIG: convert /etc/portal.yaml → pipe string IN the
#    container (pyyaml lives there). Falls back to the live $PORTAL_CONFIG env.
PORTAL_CFG="$(docker exec "$CID" python3 - <<'PY' 2>/dev/null || true
import os, sys
try:
    import yaml
    with open("/etc/portal.yaml") as f:
        apps = (yaml.safe_load(f) or {}).get("applications", {}) or {}
    parts = [f'{a.get("hostname","localhost")}:{a["external_port"]}:{a["internal_port"]}:{a.get("open_path","/")}:{name}'
             for name, a in apps.items()]
    print("|".join(parts))
except Exception:
    print(os.environ.get("PORTAL_CONFIG", ""))
PY
)"
echo "▸ rendered PORTAL_CONFIG: ${PORTAL_CFG:-<empty>}"

# 4. verdict.
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"; cleanup' EXIT
printf '%s\n' "$SS_DUMP"   > "$TMP/ss.txt"
printf '%s'   "$PORTAL_CFG" > "$TMP/portal.txt"

run_imagegen bindcheck \
    --ss "@$TMP/ss.txt" \
    --portal-config "@$TMP/portal.txt" \
    --dockerfile "$DOCKERFILE_PATH"
