#!/bin/bash

# Add llama libs after 05-configure-cuda.sh or they will be removed due to 'cuda' in path

echo "/opt/llama.cpp/cuda-${CUDA_VERSION:-12.8}" > /etc/ld.so.conf.d/50-llama.conf
ldconfig
