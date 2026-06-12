#!/bin/bash

set -euo pipefail

# Retag a docker image to one or more new repo:tag references.
#
# Uses `crane copy`, which copies the full (multi-arch) manifest
# registry-to-registry and cross-mounts existing layer blobs instead of
# streaming them through this host. Cross-repository retags are therefore
# near-instant when the registry honours blob mounts, and never slower than
# the bytes that genuinely have to move.
#
# `docker buildx imagetools create` is NOT used here: it does not attempt
# cross-repo blob mounting, so every cross-repository retag copies the entire
# image payload through this machine (multiple GB per image).
#
# Requires: crane — https://github.com/google/go-containerregistry
#   go install github.com/google/go-containerregistry/cmd/crane@latest
#   # or download the release binary and put it on PATH
#
# Usage:
#   ./retag.sh <source> <target> [<target> ...]
#
# Example:
#   ./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py312 \
#              vastai/base-image:cuda-12.9.1-auto

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <source> <target> [<target> ...]" >&2
  exit 1
fi

if ! command -v crane >/dev/null 2>&1; then
  echo "error: 'crane' not found on PATH." >&2
  echo "  install: go install github.com/google/go-containerregistry/cmd/crane@latest" >&2
  echo "  or download a release binary from https://github.com/google/go-containerregistry/releases" >&2
  exit 1
fi

SOURCE="$1"
shift

for target in "$@"; do
  echo "Retagging: ${SOURCE} -> ${target}"
  crane copy "${SOURCE}" "${target}"
done
