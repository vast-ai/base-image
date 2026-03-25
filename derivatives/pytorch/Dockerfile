ARG VAST_BASE

FROM ${VAST_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="PyTorch image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Required or we will not build - Store for use in derivative images
ARG PYTORCH_VERSION
ENV PYTORCH_VERSION=${PYTORCH_VERSION}
ARG PYTORCH_BACKEND
ENV PYTORCH_BACKEND=${PYTORCH_BACKEND}

ARG VAST_BASE
ARG TARGETARCH
COPY ./torch-companions.json /tmp/torch-companions.json
RUN \
    set -euo pipefail && \
    [[ -n "${PYTORCH_VERSION}" ]] || { echo "Must specify PYTORCH_VERSION" && exit 1; } && \
    [[ -n "${PYTORCH_BACKEND}" ]] || { echo "Must specify PYTORCH_BACKEND" && exit 1; } && \
    [[ "${PYTORCH_BACKEND}" != "auto" ]] || { echo "Cannot use 'auto' as torch backend during build" && exit 1; } && \
    # Activate the venv
    . /venv/main/bin/activate && \
    # Look up pinned companion versions from JSON
    jq -e --arg v "${PYTORCH_VERSION}" '.[$v]' /tmp/torch-companions.json > /dev/null || \
        { echo "ERROR: PyTorch ${PYTORCH_VERSION} not found in torch-companions.json" && exit 1; } && \
    # Build pinned package list, filtering amd64-only packages on other arches
    if [[ "${TARGETARCH}" == "amd64" ]]; then \
        COMPANIONS=$(jq -r --arg v "${PYTORCH_VERSION}" '.[$v].packages | to_entries | map("\(.key)==\(.value)") | .[]' /tmp/torch-companions.json | tr '\n' ' '); \
    else \
        COMPANIONS=$(jq -r --arg v "${PYTORCH_VERSION}" '(.[$v].amd64_only // []) as $excl | .[$v].packages | to_entries | map(select(.key as $k | $excl | index($k) | not)) | map("\(.key)==\(.value)") | .[]' /tmp/torch-companions.json | tr '\n' ' '); \
    fi && \
    PACKAGES="torch==${PYTORCH_VERSION} ${COMPANIONS}" && \
    echo "Installing: ${PACKAGES}" && \
    uv pip install --no-cache-dir ${PACKAGES} --torch-backend ${PYTORCH_BACKEND} && \
    # Pin numpy<2 for PyTorch <=2.1 to prevent compatibility issues.
    # NumPy 2.0 introduced breaking ABI changes that cause crashes with older PyTorch.
    # Without this pin, users could accidentally upgrade numpy and break their environment.
    major=$(echo "$PYTORCH_VERSION" | awk -F. '{print $1}') && \
    minor=$(echo "$PYTORCH_VERSION" | awk -F. '{print $2}') && \
    if (( major < 2 || (major == 2 && minor <= 1) )); then \
        pip install 'numpy<2'; \
    fi && \
    # Test 1: Verify installed package versions match expected pins
    python3 -c "import torch; assert torch.__version__.startswith('${PYTORCH_VERSION}'), f'torch: expected ${PYTORCH_VERSION}, got {torch.__version__}'" && \
    for spec in ${COMPANIONS}; do \
        pkg="${spec%%==*}" && ver="${spec#*==}" && \
        actual=$(python3 -c "import ${pkg}; print(${pkg}.__version__)") && \
        [[ "${actual}" == "${ver}" ]] || { echo "${pkg}: expected ${ver}, got ${actual}" && exit 1; } && \
        echo "${pkg}==${actual} OK"; \
    done && \
    # Test 2: Check for correct backend based on base image
    if [[ "${VAST_BASE}" == *"cuda"* ]]; then \
        python3 -c "import torch; assert torch.backends.cuda.is_built(), 'CUDA support not found in PyTorch build'" && \
        python3 -c "import torch; print('CUDA version:', torch.version.cuda)"; \
    elif [[ "${VAST_BASE}" == *"rocm"* ]]; then \
        python3 -c "import torch; assert hasattr(torch.version, 'hip') and torch.version.hip is not None, 'ROCm support not found in PyTorch build'" && \
        python3 -c "import torch; print('ROCm version:', torch.version.hip)"; \
    fi && \
    # Test 3: Basic tensor operations
    python3 -c "import torch; x = torch.rand(2,3); y = torch.rand(2,3); z = torch.mm(x, y.t()); assert z.shape == (2,2), 'Basic tensor operations failed'" && \
    # Test 4: Simple neural network instantiation and forward pass
    python3 -c "import torch; model = torch.nn.Linear(10,2); x = torch.randn(5,10); y = model(x); assert y.shape == (5,2) and not torch.isnan(y).any(), 'Neural network test failed'" && \
    rm -f /tmp/torch-companions.json

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
