#!/bin/bash

set -euo pipefail

./retag.sh vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py37 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py37
./retag.sh vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py38 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py38
./retag.sh vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py39 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py39
./retag.sh vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py310 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py310 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete
./retag.sh vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py311 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py311
./retag.sh vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py312 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py312
./retag.sh vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py313 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py313
./retag.sh vastai/base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py314 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-22.04-6.2.4-complete-py314
./retag.sh vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py37 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py37
./retag.sh vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py38 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py38
./retag.sh vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py39 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py39
./retag.sh vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py310 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py310
./retag.sh vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py311 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py311
./retag.sh vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py312 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py312 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete
./retag.sh vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py313 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py313
./retag.sh vastai/base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py314 robatvastai/prod-backup-base-image:rocm-dev-ubuntu-24.04-6.2.4-complete-py314

./retag.sh vastai/base-image:stock-ubuntu22.04-py37 robatvastai/prod-backup-base-image:stock-ubuntu22.04-py37
./retag.sh vastai/base-image:stock-ubuntu22.04-py38 robatvastai/prod-backup-base-image:stock-ubuntu22.04-py38
./retag.sh vastai/base-image:stock-ubuntu22.04-py39 robatvastai/prod-backup-base-image:stock-ubuntu22.04-py39
./retag.sh vastai/base-image:stock-ubuntu22.04-py310 robatvastai/prod-backup-base-image:stock-ubuntu22.04-py310 robatvastai/prod-backup-base-image:stock-ubuntu22.04
./retag.sh vastai/base-image:stock-ubuntu22.04-py311 robatvastai/prod-backup-base-image:stock-ubuntu22.04-py311
./retag.sh vastai/base-image:stock-ubuntu22.04-py312 robatvastai/prod-backup-base-image:stock-ubuntu22.04-py312
./retag.sh vastai/base-image:stock-ubuntu22.04-py313 robatvastai/prod-backup-base-image:stock-ubuntu22.04-py313
./retag.sh vastai/base-image:stock-ubuntu22.04-py314 robatvastai/prod-backup-base-image:stock-ubuntu22.04-py314
./retag.sh vastai/base-image:stock-ubuntu24.04-py39 robatvastai/prod-backup-base-image:stock-ubuntu24.04-py39
./retag.sh vastai/base-image:stock-ubuntu24.04-py310 robatvastai/prod-backup-base-image:stock-ubuntu24.04-py310
./retag.sh vastai/base-image:stock-ubuntu24.04-py311 robatvastai/prod-backup-base-image:stock-ubuntu24.04-py311
./retag.sh vastai/base-image:stock-ubuntu24.04-py312 robatvastai/prod-backup-base-image:stock-ubuntu24.04-py312 robatvastai/prod-backup-base-image:stock-ubuntu24.04
./retag.sh vastai/base-image:stock-ubuntu24.04-py313 robatvastai/prod-backup-base-image:stock-ubuntu24.04-py313
./retag.sh vastai/base-image:stock-ubuntu24.04-py314 robatvastai/prod-backup-base-image:stock-ubuntu24.04-py314

./retag.sh vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py37 robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py37
./retag.sh vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py38 robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py38
./retag.sh vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py39 robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py39
./retag.sh vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04
./retag.sh vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py311 robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py311
./retag.sh vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py312 robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py312
./retag.sh vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py313 robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py313
./retag.sh vastai/base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py314 robatvastai/prod-backup-base-image:cuda-11.8.0-cudnn8-devel-ubuntu22.04-py314

./retag.sh vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py37 robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py37
./retag.sh vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py38 robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py38
./retag.sh vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py39 robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py39
./retag.sh vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04
./retag.sh vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py311 robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py311
./retag.sh vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py312 robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py312
./retag.sh vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py313 robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py313
./retag.sh vastai/base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py314 robatvastai/prod-backup-base-image:cuda-12.1.1-cudnn8-devel-ubuntu22.04-py314

./retag.sh vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py37 robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py37
./retag.sh vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py38 robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py38
./retag.sh vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py39 robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py39
./retag.sh vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04
./retag.sh vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py311 robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py311
./retag.sh vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py312 robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py312
./retag.sh vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py313 robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py313
./retag.sh vastai/base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py314 robatvastai/prod-backup-base-image:cuda-12.4.1-cudnn-devel-ubuntu22.04-py314

./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py37 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py37
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py38 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py38
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py39 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py39
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py311 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py311
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py312 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py312
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py313 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py313
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py314 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu22.04-py314

./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py37 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py37
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py38 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py38
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py39 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py39
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py310 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py310
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py311 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py311
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py312 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py312 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py313 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py313
./retag.sh vastai/base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py314 robatvastai/prod-backup-base-image:cuda-12.6.3-cudnn-devel-ubuntu24.04-py314

./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py37 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py37
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py38 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py38
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py39 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py39
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py311 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py311
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py312 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py312
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py313 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py313
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py314 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu22.04-py314


./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py37 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py37
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py38 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py38
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py39 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py39
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py310 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py310
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py311 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py311
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py312 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py312 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py313 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py313
./retag.sh vastai/base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py314 robatvastai/prod-backup-base-image:cuda-12.8.1-cudnn-devel-ubuntu24.04-py314

./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py37 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py37
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py38 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py38
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py39 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py39
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py311 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py311
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py312 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py312
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py313 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py313
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py314 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu22.04-py314

./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py37 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py37
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py38 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py38
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py39 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py39
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py310 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py310
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py311 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py311
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py312 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py312 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py313 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py313
./retag.sh vastai/base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py314 robatvastai/prod-backup-base-image:cuda-12.9.1-cudnn-devel-ubuntu24.04-py314

./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py37 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py37
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py38 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py38
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py39 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py39
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py311 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py311
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py312 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py312
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py313 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py313
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py314 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04-py314

./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py37 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py37
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py38 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py38
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py39 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py39
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py310 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py310
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py311 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py311
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py312 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py312 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py313 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py313
./retag.sh vastai/base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py314 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04-py314

./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py37 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py37
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py38 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py38
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py39 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py39
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py310 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu22.04
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py311 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py311
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py312 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py312
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py313 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py313
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py314 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu22.04-py314

./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py37 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py37
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py38 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py38
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py39 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py39
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py310 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py310
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py311 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py311
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py312 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py312 robatvastai/prod-backup-base-image:cuda-13.0.1-cudnn-devel-ubuntu24.04
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py313 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py313
./retag.sh vastai/base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py314 robatvastai/prod-backup-base-image:cuda-13.0.2-cudnn-devel-ubuntu24.04-py314

./retag.sh vastai/base-image:cuda-11.8.0-auto robatvastai/prod-backup-base-image:cuda-11.8.0-auto
./retag.sh vastai/base-image:cuda-12.1.1-auto robatvastai/prod-backup-base-image:cuda-12.1.1-auto 
./retag.sh vastai/base-image:cuda-12.4.1-auto robatvastai/prod-backup-base-image:cuda-12.4.1-auto
./retag.sh vastai/base-image:cuda-12.6.3-auto robatvastai/prod-backup-base-image:cuda-12.6.3-auto
./retag.sh vastai/base-image:cuda-12.8.1-auto robatvastai/prod-backup-base-image:cuda-12.8.1-auto
./retag.sh vastai/base-image:cuda-12.9.1-auto robatvastai/prod-backup-base-image:cuda-12.9.1-auto
./retag.sh vastai/base-image:cuda-13.0.1-auto robatvastai/prod-backup-base-image:cuda-13.0.1-auto
./retag.sh vastai/base-image:cuda-13.0.2-auto robatvastai/prod-backup-base-image:cuda-13.0.2-auto


















