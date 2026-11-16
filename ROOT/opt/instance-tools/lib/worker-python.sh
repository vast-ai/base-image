#!/bin/bash
# Install the interpreter the serverless worker bootstrap asks for, at build (ADR 0058).
#
# The bootstrap creates the worker environment with `uv venv -p 3.10` from uv's managed
# Pythons. If none is installed uv downloads one at boot, after this image's CA refresh has
# run, and that interpreter's bundled pip is below the trust floor. Installed here, the
# bootstrap reuses it and lib/refresh-ca-data.sh, which every build runs after this, brings
# its bundled pip to the floor.
#
# Build-time only. Run it before lib/refresh-ca-data.sh (linter L128).
set -euo pipefail

WORKER_PYTHON=3.10

# Installing a managed 3.10 must not change which interpreter a bare `uv venv` picks.
before=$(uv python find 2>/dev/null || true)
uv python install --no-bin "$WORKER_PYTHON"
after=$(uv python find 2>/dev/null || true)
if [[ -n "$before" && "$before" != "$after" ]]; then
    echo "worker-python: installing Python ${WORKER_PYTHON} changed uv's default interpreter (${before} -> ${after})" >&2
    exit 1
fi

found=0
for py in "$(uv python dir)"/cpython-"${WORKER_PYTHON}".*/bin/python3; do
    [[ -x "$py" ]] || continue
    # uv's managed interpreters carry the pip (and its vendored certifi) of their build date.
    "$py" -m pip install --quiet --no-cache-dir --break-system-packages --root-user-action=ignore -U pip
    echo "worker-python: $("$py" --version) at ${py%/bin/python3}, $("$py" -m pip --version | cut -d' ' -f1-2)"
    found=1
done
(( found )) || { echo "worker-python: no Python ${WORKER_PYTHON} under $(uv python dir) after install" >&2; exit 1; }
