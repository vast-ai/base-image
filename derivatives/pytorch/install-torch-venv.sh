#!/bin/bash
# Install a pinned torch version into a new conda venv.
# Usage: install-torch-venv.sh <torch_version> <backend> <targetarch>
set -euo pipefail

TORCH_VER="$1"
PYTORCH_BACKEND="$2"
TARGETARCH="$3"
VENV="/venv/torch-${TORCH_VER}"

echo "=== Creating venv for torch ${TORCH_VER} at ${VENV} ==="
/opt/miniforge3/bin/conda create -p "${VENV}" \
    python="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" -y

# Look up pinned companion versions, filtering amd64-only packages on other arches
COMPANIONS=$(jq -r --arg v "${TORCH_VER}" --arg arch "${TARGETARCH}" \
    'if .[$v] == null then error("version not found") else . end |
     (.[$v].amd64_only // []) as $excl |
     .[$v].packages | to_entries |
     if $arch != "amd64" then map(select(.key as $k | $excl | index($k) | not)) else . end |
     map(.key + "==" + .value) | .[]' \
    /tmp/torch-companions.json | tr '\n' ' ')

PACKAGES="torch==${TORCH_VER} ${COMPANIONS}"
echo "Installing: ${PACKAGES}"
uv pip install --no-cache-dir --python "${VENV}/bin/python" ${PACKAGES} --torch-backend "${PYTORCH_BACKEND}"

# Add activate script matching /venv/main pattern
sed "s|realpath /venv/main|realpath ${VENV}|" /venv/main/bin/activate > "${VENV}/bin/activate"

# Verify versions
"${VENV}/bin/python" -c "import torch; assert torch.__version__.startswith('${TORCH_VER}'), f'torch: expected ${TORCH_VER}, got {torch.__version__}'"
for spec in ${COMPANIONS}; do
    pkg="${spec%%==*}" && ver="${spec#*==}"
    actual=$("${VENV}/bin/python" -c "from importlib.metadata import version; print(version('${pkg}'))")
    [[ "${actual}" == "${ver}"* ]] || { echo "${pkg}: expected ${ver}, got ${actual}" && exit 1; }
    echo "${pkg}==${actual} OK"
done

/opt/miniforge3/bin/conda clean -ay
echo "=== torch ${TORCH_VER} installed at ${VENV} ==="
