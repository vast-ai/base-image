#!/bin/bash
# Test: Python ecosystem — interpreters, venvs, package managers.
source "$(dirname "$0")/../lib.sh"

# System python3
python3 --version &>/dev/null || test_fail "python3 not available"
echo "  python3: $(python3 --version 2>&1)"

# uv package manager
uv --version &>/dev/null || test_fail "uv not available"
echo "  uv: $(uv --version 2>&1)"

# Functional check: uv pip can resolve a package (dry-run, no install)
if uv pip install --dry-run pip 2>/dev/null | grep -qi "would\|already\|satisfied\|pip"; then
    echo "  uv pip install: functional (dry-run ok)"
else
    echo "  WARN: uv pip install --dry-run did not produce expected output"
fi

# Main venv (skip-if-absent)
if [[ -d /venv/main ]]; then
    source /venv/main/bin/activate 2>/dev/null || test_fail "cannot activate /venv/main"
    venv_version=$(python --version 2>&1 | grep -oP '[0-9]+\.[0-9]+')
    echo "  venv python: $(python --version 2>&1)"
    python -m pip --version &>/dev/null || echo "  WARN: pip not available in venv"
    # If PYTHON_VERSION is set, venv python should match (major.minor)
    if [[ -n "${PYTHON_VERSION:-}" ]]; then
        expected=$(echo "$PYTHON_VERSION" | grep -oP '^[0-9]+\.[0-9]+')
        if [[ "$venv_version" == "$expected" ]]; then
            echo "  PYTHON_VERSION=${PYTHON_VERSION} matches venv (${venv_version})"
        else
            test_fail "PYTHON_VERSION=${PYTHON_VERSION} (${expected}) does not match venv python (${venv_version})"
        fi
    fi
    deactivate 2>/dev/null
else
    if is_vast_image; then
        test_fail "/venv/main not found (required for IMAGE_TYPE=vast)"
    fi
    echo "  absent (ok): /venv/main"
fi

# Miniforge/conda (skip-if-absent)
if [[ -d /opt/miniforge3 ]]; then
    if /opt/miniforge3/bin/conda --version &>/dev/null; then
        echo "  conda: $(/opt/miniforge3/bin/conda --version 2>&1)"
        # Verify conda can list envs (functional check)
        env_count=$(/opt/miniforge3/bin/conda env list 2>/dev/null | grep -c '^\S' || true)
        echo "  conda envs: ${env_count}"
        # Verify mamba solver is available (faster dependency resolution)
        if /opt/miniforge3/bin/conda list -n base 2>/dev/null | grep -q mamba; then
            echo "  mamba solver: available"
        fi
    else
        echo "  WARN: miniforge3 present but conda not working"
    fi
else
    if is_vast_image; then
        test_fail "/opt/miniforge3 not found (required for IMAGE_TYPE=vast)"
    fi
    echo "  absent (ok): /opt/miniforge3"
fi

# Jupyter kernels (skip if jupyter not installed)
if command -v jupyter &>/dev/null; then
    kernel_count=$(jupyter kernelspec list 2>/dev/null | grep -c "  ")
    echo "  jupyter kernels: ${kernel_count}"
else
    if is_vast_image; then
        test_fail "jupyter not found (required for IMAGE_TYPE=vast)"
    fi
    echo "  absent (ok): jupyter"
fi

# Portal venv fastapi (skip-if-absent)
if [[ -d /opt/portal-aio/venv ]]; then
    /opt/portal-aio/venv/bin/python -c "import fastapi" 2>/dev/null \
        && echo "  portal venv: fastapi importable" \
        || echo "  WARN: portal venv present but fastapi not importable"
else
    if is_vast_image; then
        test_fail "/opt/portal-aio/venv not found (required for IMAGE_TYPE=vast)"
    fi
    echo "  absent (ok): /opt/portal-aio/venv"
fi

test_pass "python ecosystem verified"
