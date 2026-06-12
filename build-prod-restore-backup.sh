#!/bin/bash

set -euo pipefail

./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py37 vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py37
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py38 vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py38
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py39 vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py39
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py310 vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py310 vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py311 vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py311
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py312 vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py312
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py313 vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py313
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py314 vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py314
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py37 vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py37
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py38 vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py38
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py39 vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py39
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py310 vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py310
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py311 vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py311
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py312 vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py312 vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py313 vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py313
./retag.sh robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py314 vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py314

./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu22.04-py37 vastai/base-image:stock-ubuntu22.04-py37
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu22.04-py38 vastai/base-image:stock-ubuntu22.04-py38
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu22.04-py39 vastai/base-image:stock-ubuntu22.04-py39
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu22.04-py310 vastai/base-image:stock-ubuntu22.04-py310 vastai/base-image:stock-ubuntu22.04
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu22.04-py311 vastai/base-image:stock-ubuntu22.04-py311
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu22.04-py312 vastai/base-image:stock-ubuntu22.04-py312
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu22.04-py313 vastai/base-image:stock-ubuntu22.04-py313
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu22.04-py314 vastai/base-image:stock-ubuntu22.04-py314
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu24.04-py39 vastai/base-image:stock-ubuntu24.04-py39
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu24.04-py310 vastai/base-image:stock-ubuntu24.04-py310
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu24.04-py311 vastai/base-image:stock-ubuntu24.04-py311
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu24.04-py312 vastai/base-image:stock-ubuntu24.04-py312 vastai/base-image:stock-ubuntu24.04
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu24.04-py313 vastai/base-image:stock-ubuntu24.04-py313
./retag.sh robatvastai/prod-backup-base-image:stock-ubuntu24.04-py314 vastai/base-image:stock-ubuntu24.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py37 vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py38 vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py39 vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py310 vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py310 vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04
./retag.sh robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py311 vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py312 vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py312
./retag.sh robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py313 vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py314 vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py37 vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py38 vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py39 vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py310 vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py310 vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04
./retag.sh robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py311 vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py312 vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py312
./retag.sh robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py313 vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py314 vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py37 vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py38 vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py39 vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04
./retag.sh robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py311 vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py312 vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py312
./retag.sh robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py313 vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py314 vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py37 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py38 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py39 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py311 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py312 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py312
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py313 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py314 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py37 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py38 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py39 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py310 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py310
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py311 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py312 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py312 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py313 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py314 vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py37 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py38 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py39 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py311 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py312 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py312
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py313 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py314 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py314


./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py37 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py38 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py39 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py310 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py310
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py311 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py312 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py312 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py313 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py314 vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py37 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py38 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py39 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py311 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py312 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py312
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py313 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py314 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py37 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py38 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py39 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py310 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py310
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py311 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py312 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py312 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py313 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py314 vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py37 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py38 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py39 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py311 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py312 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py312
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py313 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py314 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py37 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py38 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py39 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py310 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py310
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py311 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py312 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py312 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py313 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py314 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py37 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py38 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py39 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py310 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py311 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py312 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py312
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py313 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py314 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py37 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py37
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py38 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py38
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py39 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py39
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py310 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py310
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py311 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py311
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py312 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py312 vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py313 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py313
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py314 vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py314

./retag.sh robatvastai/prod-backup-base-image:cuda-11.8.0-auto vastai/base-image:cuda-11.8.0-auto
./retag.sh robatvastai/prod-backup-base-image:cuda-12.1.1-auto vastai/base-image:cuda-12.1.1-auto 
./retag.sh robatvastai/prod-backup-base-image:cuda-12.4.1-auto vastai/base-image:cuda-12.4.1-auto
./retag.sh robatvastai/prod-backup-base-image:cuda-12.6.3-auto vastai/base-image:cuda-12.6.3-auto
./retag.sh robatvastai/prod-backup-base-image:cuda-12.8.1-auto vastai/base-image:cuda-12.8.1-auto
./retag.sh robatvastai/prod-backup-base-image:cuda-12.9.1-auto vastai/base-image:cuda-12.9.1-auto
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.1-auto vastai/base-image:cuda-13.0.1-auto
./retag.sh robatvastai/prod-backup-base-image:cuda-13.0.2-auto vastai/base-image:cuda-13.0.2-auto


















