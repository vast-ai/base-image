#!/bin/bash

docker build --build-arg VAST_BASE=vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04 \
    --tag vastai/linux-desktop:cuda-12.1-ubuntu-22.04 . --push && \

docker build --build-arg VAST_BASE=vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04 \
    --tag vastai/linux-desktop:cuda-12.4-ubuntu-22.04 . --push && \

docker build --build-arg VAST_BASE=vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04 \
    --tag vastai/linux-desktop:cuda-12.6-ubuntu-22.04 . --push && \

docker build --build-arg VAST_BASE=vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04 \
    --tag vastai/linux-desktop:cuda-12.6-ubuntu-24.04 . --push && \

docker build --build-arg VAST_BASE=vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04 \
    --tag vastai/linux-desktop:cuda-12.8-ubuntu-24.04 . --push


