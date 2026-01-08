#!/bin/bash
set -euo pipefail

. /venv/main/bin/activate

# Add additional python packages if declared
if [[ -n "${PIP_INSTALL:-}" ]]; then
    uv pip install ${PIP_INSTALL}
fi

# Install Deep GEMM if specified - Required for Deepseek sparse attn
install_deepgemm=${INSTALL_DEEPGEMM:-false}
if [[ "${install_deepgemm,,}" = "true" ]]; then
    cd /tmp
    git clone --recursive https://github.com/deepseek-ai/DeepGEMM.git
    cd DeepGEMM
    ./install.sh
fi