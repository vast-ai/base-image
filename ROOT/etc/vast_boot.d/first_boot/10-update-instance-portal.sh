#!/bin/bash

# Update the instance portal if the installed version differs from the target.
#
# Target version is determined by (in priority order):
#   1. PORTAL_VERSION env var (user pinned a specific version)
#   2. Latest GitHub release from PORTAL_REPO (defaults to vast-ai/base-image)
#
# The installed version is read from /opt/portal-aio/VERSION.
# Updates happen on mismatch — upgrade or downgrade — but never when versions match.

# Respect --no-update-portal flag from boot_default.sh
if [[ "${update_portal}" == "false" ]]; then
    echo "Portal update disabled (--no-update-portal or serverless mode)"
    return 0
fi

PORTAL_DIR="/opt/portal-aio"
VERSION_FILE="${PORTAL_DIR}/VERSION"
REPO="${PORTAL_REPO:-vast-ai/base-image}"

# Read installed version from the VERSION file shipped with the portal
INSTALLED=""
if [[ -f "$VERSION_FILE" ]]; then
    INSTALLED=$(<"$VERSION_FILE")
    INSTALLED="${INSTALLED%%[[:space:]]*}"  # trim trailing whitespace
fi

if [[ -z "$INSTALLED" ]]; then
    echo "No portal VERSION file found — portal not installed, skipping update"
    return 0
fi

# Determine the target version
if [[ -n "${PORTAL_VERSION}" ]]; then
    # User explicitly pinned a version via env var
    TARGET="${PORTAL_VERSION}"
    echo "Portal target version (from PORTAL_VERSION env): ${TARGET}"
else
    # Fetch the latest release from GitHub
    TARGET=$(curl -sf --max-time 10 \
        "https://api.github.com/repos/${REPO}/releases/latest" | jq -r .tag_name)

    if [[ -z "$TARGET" || "$TARGET" == "null" ]]; then
        echo "Could not fetch latest portal version from GitHub, skipping update"
        return 0
    fi
    echo "Portal target version (from GitHub latest): ${TARGET}"
fi

# Normalize: ensure both have 'v' prefix for comparison
[[ "$INSTALLED" != v* ]] && INSTALLED="v${INSTALLED}"
[[ "$TARGET" != v* ]] && TARGET="v${TARGET}"

if [[ "$INSTALLED" == "$TARGET" ]]; then
    echo "Portal is up to date (${INSTALLED})"
    return 0
fi

echo "Portal version mismatch: installed=${INSTALLED}, target=${TARGET}"
update-portal -v "$TARGET"
