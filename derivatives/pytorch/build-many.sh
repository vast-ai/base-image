#!/bin/bash

set -e

BASE_REPO=${BASE_REPO:-vastai/base-image}
TAG_REPO=${TAG_REPO:-vastai/pytorch}
DOCKERFILE=${DOCKERFILE:-Dockerfile}

main() {
    build_image --torch_ver=2.4.1 --python_ver=310 --cuda_ver=12.1.1-cudnn8-devel --torch_backend=cu121
    build_image --torch_ver=2.4.1 --python_ver=311 --cuda_ver=12.1.1-cudnn8-devel --torch_backend=cu121
    build_image --torch_ver=2.4.1 --python_ver=312 --cuda_ver=12.1.1-cudnn8-devel --torch_backend=cu121
    build_image --torch_ver=2.4.1 --python_ver=310 --cuda_ver=12.4.1-cudnn-devel --torch_backend=cu124 --multi_arch
    build_image --torch_ver=2.4.1 --python_ver=311 --cuda_ver=12.4.1-cudnn-devel --torch_backend=cu124 --multi_arch
    build_image --torch_ver=2.4.1 --python_ver=312 --cuda_ver=12.4.1-cudnn-devel --torch_backend=cu124
    build_image --torch_ver=2.5.1 --python_ver=310 --cuda_ver=12.1.1-cudnn8-devel --torch_backend=cu121
    build_image --torch_ver=2.5.1 --python_ver=311 --cuda_ver=12.1.1-cudnn8-devel --torch_backend=cu121
    build_image --torch_ver=2.5.1 --python_ver=312 --cuda_ver=12.1.1-cudnn8-devel --torch_backend=cu121

    build_image --torch_ver=2.5.1 --python_ver=310 --cuda_ver=12.4.1-cudnn-devel --torch_backend=cu124 --multi_arch
    build_image --torch_ver=2.5.1 --python_ver=311 --cuda_ver=12.4.1-cudnn-devel --torch_backend=cu124 --multi_arch
    build_image --torch_ver=2.5.1 --python_ver=312 --cuda_ver=12.4.1-cudnn-devel --torch_backend=cu124

    build_image --torch_ver=2.6.0 --python_ver=310 --cuda_ver=11.8.0-cudnn8-devel --torch_backend=cu118
    build_image --torch_ver=2.6.0 --python_ver=311 --cuda_ver=11.8.0-cudnn8-devel --torch_backend=cu118
    build_image --torch_ver=2.6.0 --python_ver=312 --cuda_ver=11.8.0-cudnn8-devel --torch_backend=cu118
    build_image --torch_ver=2.6.0 --python_ver=313 --cuda_ver=11.8.0-cudnn8-devel --torch_backend=cu118
    build_image --torch_ver=2.6.0 --python_ver=310 --cuda_ver=12.4.1-cudnn-devel --torch_backend=cu124
    build_image --torch_ver=2.6.0 --python_ver=311 --cuda_ver=12.4.1-cudnn-devel --torch_backend=cu124
    build_image --torch_ver=2.6.0 --python_ver=312 --cuda_ver=12.4.1-cudnn-devel --torch_backend=cu124
    build_image --torch_ver=2.6.0 --python_ver=313 --cuda_ver=12.4.1-cudnn-devel --torch_backend=cu124
    build_image --torch_ver=2.6.0 --python_ver=310 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126 --multi_arch
    build_image --torch_ver=2.6.0 --python_ver=311 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126 --multi_arch
    build_image --torch_ver=2.6.0 --python_ver=312 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126 --multi_arch
    build_image --torch_ver=2.6.0 --python_ver=313 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126 --multi_arch

    build_image --torch_ver=2.7.1 --python_ver=310 --cuda_ver=11.8.0-cudnn8-devel --torch_backend=cu118
    build_image --torch_ver=2.7.1 --python_ver=311 --cuda_ver=11.8.0-cudnn8-devel --torch_backend=cu118
    build_image --torch_ver=2.7.1 --python_ver=312 --cuda_ver=11.8.0-cudnn8-devel --torch_backend=cu118
    build_image --torch_ver=2.7.1 --python_ver=313 --cuda_ver=11.8.0-cudnn8-devel --torch_backend=cu118
    build_image --torch_ver=2.7.1 --python_ver=310 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126
    build_image --torch_ver=2.7.1 --python_ver=311 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126
    build_image --torch_ver=2.7.1 --python_ver=312 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126
    build_image --torch_ver=2.7.1 --python_ver=313 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126
    build_image --torch_ver=2.7.1 --python_ver=310 --cuda_ver=12.8.1-cudnn-devel --torch_backend=cu128 --multi_arch
    build_image --torch_ver=2.7.1 --python_ver=311 --cuda_ver=12.8.1-cudnn-devel --torch_backend=cu128 --multi_arch
    build_image --torch_ver=2.7.1 --python_ver=312 --cuda_ver=12.8.1-cudnn-devel --torch_backend=cu128 --multi_arch
    build_image --torch_ver=2.7.1 --python_ver=313 --cuda_ver=12.8.1-cudnn-devel --torch_backend=cu128 --multi_arch

    build_image --torch_ver=2.8.0 --python_ver=310 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126
    build_image --torch_ver=2.8.0 --python_ver=311 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126
    build_image --torch_ver=2.8.0 --python_ver=312 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126
    build_image --torch_ver=2.8.0 --python_ver=313 --cuda_ver=12.6.3-cudnn-devel --torch_backend=cu126
    build_image --torch_ver=2.8.0 --python_ver=310 --cuda_ver=12.8.1-cudnn-devel --torch_backend=cu128
    build_image --torch_ver=2.8.0 --python_ver=311 --cuda_ver=12.8.1-cudnn-devel --torch_backend=cu128
    build_image --torch_ver=2.8.0 --python_ver=312 --cuda_ver=12.8.1-cudnn-devel --torch_backend=cu128
    build_image --torch_ver=2.8.0 --python_ver=313 --cuda_ver=12.8.1-cudnn-devel --torch_backend=cu128
    build_image --torch_ver=2.8.0 --python_ver=310 --cuda_ver=12.9.1-cudnn-devel --torch_backend=cu129 --multi_arch
    build_image --torch_ver=2.8.0 --python_ver=311 --cuda_ver=12.9.1-cudnn-devel --torch_backend=cu129 --multi_arch
    build_image --torch_ver=2.8.0 --python_ver=312 --cuda_ver=12.9.1-cudnn-devel --torch_backend=cu129 --multi_arch
    build_image --torch_ver=2.8.0 --python_ver=313 --cuda_ver=12.9.1-cudnn-devel --torch_backend=cu129 --multi_arch
}

log_error() {
   echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"
}

build_image() {
    local torch_ver=""
    local torch_ver_override=""
    local python_ver=""
    local cuda_ver=""
    local torch_backend=""
    local multi_arch=false
    local platform="linux/amd64"
    local tags=""
    
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --torch_ver=*)
                torch_ver="${1#*=}"
                shift
                ;;
            --torch_ver_override=*)
                torch_ver_override="${1#*=}"
                shift
                ;;
            --python_ver=*)
                python_ver="${1#*=}"
                shift
                ;;
            --cuda_ver=*)
                cuda_ver="${1#*=}"
                shift
                ;;
            --torch_backend=*)
                torch_backend="${1#*=}"
                shift
                ;;
            --multi_arch)
                multi_arch=true
                platform="linux/amd64,linux/arm64"
                shift
                ;;
            *)
                echo "Unknown parameter: $1"
                return 1
                ;;
        esac
    done
    
    # Validate required parameters
    if [[ -z "$torch_ver" ]] || [[ -z "$python_ver" ]] || [[ -z "$cuda_ver" ]] || [[ -z "$torch_backend" ]]; then
        echo "Error: Missing required parameters"
        echo "Usage: build-many.sh --torch_ver=<ver> --python_ver=<ver> --cuda_ver=<ver> --torch_backend=<backend> [--multi_arch]"
        return 1
    fi

    # Set up tags based on python version
    tags="--tag ${TAG_REPO}:${torch_ver_override:-$torch_ver}-cuda-${cuda_ver%%-*}-py${python_ver}-22.04"
    if [[ "$python_ver" == "310" ]]; then
        tags+=" --tag ${TAG_REPO}:${torch_ver_override:-$torch_ver}-cuda-${cuda_ver%%-*}-22.04"
    fi
    
    # Execute docker buildx command
    echo "Building ${cuda_ver%%-*} Torch ${torch_ver} with Python ${python_ver}"

    docker run --privileged --rm tonistiigi/binfmt:latest --uninstall qemu-*
    docker run --privileged --rm tonistiigi/binfmt:qemu-v6.2.0 --install all
    
    docker buildx build \
        --progress=plain \
        --platform ${platform} \
        -f ${DOCKERFILE:-Dockerfile} \
        --build-arg VAST_BASE="${BASE_REPO}:${torch_ver_override:-$torch_ver}-cuda-${cuda_ver%%-*}-py${python_ver}-22.04" \
        --build-arg PYTORCH_VERSION="${torch_ver}" \
        --build-arg PYTORCH_BACKEND="${torch_backend}" . \
        ${tags} \
        --push || {
            log_error "Failed building for CUDA ${cuda_ver%%-*} Torch ${torch_ver} with Python ${python_ver}"
            return 1
        }
}

main
