#!/bin/bash

# Only attempt portal upgrade if PORTAL_VERSION is set in the image
if [[ -z "${PORTAL_VERSION}" ]]; then
    echo "PORTAL_VERSION not set, skipping portal update check"
    return 0
fi

REPO="${PORTAL_REPO:-vast-ai/base-image}"
LATEST_VERSION=$(curl -sf --max-time 10 https://api.github.com/repos/$REPO/releases/latest | jq -r .tag_name)

if [[ -z "$LATEST_VERSION" || "$LATEST_VERSION" == "null" ]]; then
    echo "Could not fetch latest portal version from GitHub, skipping update"
    return 0
fi

# Normalize: ensure both have 'v' prefix for comparison
CURRENT="$PORTAL_VERSION"
[[ "$CURRENT" != v* ]] && CURRENT="v$CURRENT"

if [[ "$CURRENT" == "$LATEST_VERSION" ]]; then
    echo "Portal is up to date ($CURRENT)"
    return 0
fi

echo "Portal update available: $CURRENT -> $LATEST_VERSION"
update-portal -v "$LATEST_VERSION"
