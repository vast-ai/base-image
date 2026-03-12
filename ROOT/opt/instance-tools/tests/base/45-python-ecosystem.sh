#!/bin/bash
# Test: Python ecosystem — interpreters, venvs, package managers.
source "$(dirname "$0")/../lib.sh"

# System python3
python3 --version &>/dev/null || test_fail "python3 not available"
echo "  python3: $(python3 --version 2>&1)"

# uv package manager
uv --version &>/dev/null || test_fail "uv not available"
echo "  uv: $(uv --version 2>&1)"

# Main venv (skip-if-absent)
if [[ -d /venv/main ]]; then
    source /venv/main/bin/activate 2>/dev/null || test_fail "cannot activate /venv/main"
    echo "  venv python: $(python --version 2>&1)"
    python -m pip --version &>/dev/null || echo "  WARN: pip not available in venv"
    deactivate 2>/dev/null
else
    echo "  absent (ok): /venv/main"
fi

# Miniforge/conda (skip-if-absent)
if [[ -d /opt/miniforge3 ]]; then
    /opt/miniforge3/bin/conda --version &>/dev/null && echo "  conda: $(/opt/miniforge3/bin/conda --version 2>&1)" \
        || echo "  WARN: miniforge3 present but conda not working"
else
    echo "  absent (ok): /opt/miniforge3"
fi

# Jupyter kernels (skip if jupyter not installed)
if command -v jupyter &>/dev/null; then
    kernel_count=$(jupyter kernelspec list 2>/dev/null | grep -c "  ")
    echo "  jupyter kernels: ${kernel_count}"
else
    echo "  absent (ok): jupyter"
fi

# Portal venv fastapi (skip-if-absent)
if [[ -d /opt/portal-aio/venv ]]; then
    /opt/portal-aio/venv/bin/python -c "import fastapi" 2>/dev/null \
        && echo "  portal venv: fastapi importable" \
        || echo "  WARN: portal venv present but fastapi not importable"
else
    echo "  absent (ok): /opt/portal-aio/venv"
fi

test_pass "python ecosystem verified"
