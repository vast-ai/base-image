ARG VAST_BASE

FROM ${VAST_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="PyTorch image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build - Store for use in derivative images
ARG PYTORCH_VERSION
ENV PYTORCH_VERSION=${PYTORCH_VERSION}
ARG PYTORCH_BACKEND
ENV PYTORCH_BACKEND=${PYTORCH_BACKEND}

ARG VAST_BASE
RUN \
    set -euo pipefail && \
    [[ -n "${PYTORCH_VERSION}" ]] || { echo "Must specify PYTORCH_VERSION" && exit 1; } && \
    [[ -n "${PYTORCH_BACKEND}" ]] || { echo "Must specify PYTORCH_BACKEND" && exit 1; } && \
    [[ "${PYTORCH_BACKEND}" != "auto" ]] || { echo "Cannot use 'auto' as torch backend during build" && exit 1; } && \
    # Activate the venv
    . /venv/main/bin/activate && \
    uv pip install \
        torch==${PYTORCH_VERSION} \
        torchvision \
        torchaudio \
        torchcodec \
        torchtext \
        torchdata \
        --torch-backend ${PYTORCH_BACKEND} && \
    # Pin numpy<2 for PyTorch <=2.1 to prevent compatibility issues.
    # NumPy 2.0 introduced breaking ABI changes that cause crashes with older PyTorch.
    # Without this pin, users could accidentally upgrade numpy and break their environment.
    major=$(echo "$PYTORCH_VERSION" | awk -F. '{print $1}') && \
    minor=$(echo "$PYTORCH_VERSION" | awk -F. '{print $2}') && \
    if (( major < 2 || (major == 2 && minor <= 1) )); then \
        pip install 'numpy<2'; \
    fi && \
    # Test 1: Verify PyTorch version (using startswith to handle CUDA/ROCm suffixes)
    python3 -c "import torch; assert torch.__version__.startswith('${PYTORCH_VERSION}'), f'Version mismatch: expected version starting with ${PYTORCH_VERSION}, got {torch.__version__}'" && \
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
    python3 -c "import torch; model = torch.nn.Linear(10,2); x = torch.randn(5,10); y = model(x); assert y.shape == (5,2) and not torch.isnan(y).any(), 'Neural network test failed'"

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash