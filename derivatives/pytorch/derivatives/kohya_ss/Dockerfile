ARG PYTORCH_BASE=vastai/pytorch:cuda-12.8.1-auto

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Stable Diffusion Kohyas GUI image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG KOHYA_REF
ARG TORCH_BACKEND=cu128
# Kohya's GUI provides some great install scripts for local use, but we will manually configure everything we need
RUN \
    [[ -n "${KOHYA_REF}" ]] || { echo "Must specify KOHYA_REF" && exit 1; } && \
    . /venv/main/bin/activate && \
    # We have PyTorch pre-installed so we will check at the end of the install that it has not been clobbered
    torch_version_pre="$(python -c 'import torch; print (torch.__version__)')" && \
    # Install xformers while pinning to the inherited torch version.  Fail build on dependency resolution if matching version is unavailable
    uv pip install xformers torch==$PYTORCH_VERSION torchvision torchaudio torchcodec --torch-backend "${TORCH_BACKEND}" --index-strategy unsafe-best-match && \
    # Get Kohya and install dependencies (We will unpin torch & xformers)
    cd /opt/workspace-internal/ && \
    git clone --recursive https://github.com/bmaltais/kohya_ss.git && \
    cd /opt/workspace-internal/kohya_ss && \
    git checkout "${KOHYA_REF}" && \
    git submodule update --init --recursive && \
    # Allow installation with alternative torch versions and disable bundled tensorboard
    grep -v "^--extra-index-url\|^torch==\|^torchvision==\|^xformers==\|^tensorboard\|^tensorflow" requirements_linux.txt > requirements_linux_modified.txt && \
    uv pip install --no-cache-dir \
        torch==${PYTORCH_VERSION} \
        -r requirements_linux_modified.txt && \
    rm -f requirements_linux_modified.txt && \
    # Fix the Tensorboard URL in the GUI to avoid hardcoded localhost links
    sed -i 's|link=f"http://localhost:{self.tensorboard_port}"|link=f"http://{os.environ.get('\''PUBLIC_IPADDR'\'')}:{os.environ.get('\''VAST_TCP_PORT_6006'\'')}"|' \
        /opt/workspace-internal/kohya_ss/kohya_gui/class_tensorboard.py && \
    # Set up a default accelerate profile
    mkdir -p "/root/.cache/huggingface/accelerate" && \
    cp config_files/accelerate/default_config.yaml /root/.cache/huggingface/accelerate/default_config.yaml && \
    # Test 1: Verify PyTorch version is unaltered
    torch_version_post="$(python -c 'import torch; print (torch.__version__)')" && \
    [[ $torch_version_pre = $torch_version_post ]] || { echo "PyTorch version mismatch (wanted ${torch_version_pre} but got ${torch_version_post})"; exit 1; }
