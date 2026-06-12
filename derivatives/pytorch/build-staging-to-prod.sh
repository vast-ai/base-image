#!/bin/bash

set -eou pipefail

# ../../retag.sh robatvastai/pytorch:2.4.1-cuda-12.1.1-py310-22.04 vastai/pytorch:2.4.1-cuda-12.1.1-py310-22.04 vastai/pytorch:2.4.1-cuda-12.1.1-22.04
# ../../retag.sh robatvastai/pytorch:2.4.1-cuda-12.1.1-py311-22.04 vastai/pytorch:2.4.1-cuda-12.1.1-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.4.1-cuda-12.1.1-py312-22.04 vastai/pytorch:2.4.1-cuda-12.1.1-py312-22.04
# ../../retag.sh robatvastai/pytorch:2.4.1-cuda-12.4.1-py310-22.04 vastai/pytorch:2.4.1-cuda-12.4.1-py310-22.04 vastai/pytorch:2.4.1-cuda-12.4.1-22.04
# ../../retag.sh robatvastai/pytorch:2.4.1-cuda-12.4.1-py311-22.04 vastai/pytorch:2.4.1-cuda-12.4.1-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.4.1-cuda-12.4.1-py312-22.04 vastai/pytorch:2.4.1-cuda-12.4.1-py312-22.04

# ../../retag.sh robatvastai/pytorch:2.5.1-cuda-12.1.1-py310-22.04 vastai/pytorch:2.5.1-cuda-12.1.1-py310-22.04 vastai/pytorch:2.5.1-cuda-12.1.1-22.04
# ../../retag.sh robatvastai/pytorch:2.5.1-cuda-12.1.1-py311-22.04 vastai/pytorch:2.5.1-cuda-12.1.1-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.5.1-cuda-12.1.1-py312-22.04 vastai/pytorch:2.5.1-cuda-12.1.1-py312-22.04
# ../../retag.sh robatvastai/pytorch:2.5.1-cuda-12.4.1-py310-22.04 vastai/pytorch:2.5.1-cuda-12.4.1-py310-22.04 vastai/pytorch:2.5.1-cuda-12.4.1-22.04
# ../../retag.sh robatvastai/pytorch:2.5.1-cuda-12.4.1-py311-22.04 vastai/pytorch:2.5.1-cuda-12.4.1-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.5.1-cuda-12.4.1-py312-22.04 vastai/pytorch:2.5.1-cuda-12.4.1-py312-22.04

# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-11.8.0-py310-22.04 vastai/pytorch:2.6.0-cuda-11.8.0-py310-22.04 vastai/pytorch:2.6.0-cuda-11.8.0-22.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-11.8.0-py311-22.04 vastai/pytorch:2.6.0-cuda-11.8.0-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-11.8.0-py312-22.04 vastai/pytorch:2.6.0-cuda-11.8.0-py312-22.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-11.8.0-py313-22.04 vastai/pytorch:2.6.0-cuda-11.8.0-py313-22.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.4.1-py310-22.04 vastai/pytorch:2.6.0-cuda-12.4.1-py310-22.04 vastai/pytorch:2.6.0-cuda-12.4.1-22.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.4.1-py311-22.04 vastai/pytorch:2.6.0-cuda-12.4.1-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.4.1-py312-22.04 vastai/pytorch:2.6.0-cuda-12.4.1-py312-22.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.4.1-py313-22.04 vastai/pytorch:2.6.0-cuda-12.4.1-py313-22.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.6.3-py310-22.04 vastai/pytorch:2.6.0-cuda-12.6.3-py310-22.04 vastai/pytorch:2.6.0-cuda-12.6.3-22.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.6.3-py311-22.04 vastai/pytorch:2.6.0-cuda-12.6.3-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.6.3-py312-22.04 vastai/pytorch:2.6.0-cuda-12.6.3-py312-22.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.6.3-py313-22.04 vastai/pytorch:2.6.0-cuda-12.6.3-py313-22.04

# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.6.3-py310-24.04 vastai/pytorch:2.6.0-cuda-12.6.3-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.6.3-py311-24.04 vastai/pytorch:2.6.0-cuda-12.6.3-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.6.3-py312-24.04 vastai/pytorch:2.6.0-cuda-12.6.3-py312-24.04 vastai/pytorch:2.6.0-cuda-12.6.3-24.04
# ../../retag.sh robatvastai/pytorch:2.6.0-cuda-12.6.3-py313-24.04 vastai/pytorch:2.6.0-cuda-12.6.3-py313-24.04

# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-11.8.0-py310-22.04 vastai/pytorch:2.7.1-cuda-11.8.0-py310-22.04 vastai/pytorch:2.7.1-cuda-11.8.0-22.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-11.8.0-py311-22.04 vastai/pytorch:2.7.1-cuda-11.8.0-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-11.8.0-py312-22.04 vastai/pytorch:2.7.1-cuda-11.8.0-py312-22.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-11.8.0-py313-22.04 vastai/pytorch:2.7.1-cuda-11.8.0-py313-22.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.6.3-py310-22.04 vastai/pytorch:2.7.1-cuda-12.6.3-py310-22.04 vastai/pytorch:2.7.1-cuda-12.6.3-22.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.6.3-py311-22.04 vastai/pytorch:2.7.1-cuda-12.6.3-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.6.3-py312-22.04 vastai/pytorch:2.7.1-cuda-12.6.3-py312-22.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.6.3-py313-22.04 vastai/pytorch:2.7.1-cuda-12.6.3-py313-22.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.8.1-py310-22.04 vastai/pytorch:2.7.1-cuda-12.8.1-py310-22.04 vastai/pytorch:2.7.1-cuda-12.8.1-22.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.8.1-py311-22.04 vastai/pytorch:2.7.1-cuda-12.8.1-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.8.1-py312-22.04 vastai/pytorch:2.7.1-cuda-12.8.1-py312-22.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.8.1-py313-22.04 vastai/pytorch:2.7.1-cuda-12.8.1-py313-22.04

# ../../retag.sh robatvastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py310 vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py310
# ../../retag.sh robatvastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py311 vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py311
# ../../retag.sh robatvastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312 vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312

# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.6.3-py310-24.04 vastai/pytorch:2.7.1-cuda-12.6.3-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.6.3-py311-24.04 vastai/pytorch:2.7.1-cuda-12.6.3-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.6.3-py312-24.04 vastai/pytorch:2.7.1-cuda-12.6.3-py312-24.04 vastai/pytorch:2.7.1-cuda-12.6.3-24.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.6.3-py313-24.04 vastai/pytorch:2.7.1-cuda-12.6.3-py313-24.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.8.1-py310-24.04 vastai/pytorch:2.7.1-cuda-12.8.1-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.8.1-py311-24.04 vastai/pytorch:2.7.1-cuda-12.8.1-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.8.1-py312-24.04 vastai/pytorch:2.7.1-cuda-12.8.1-py312-24.04 vastai/pytorch:2.7.1-cuda-12.8.1-24.04
# ../../retag.sh robatvastai/pytorch:2.7.1-cuda-12.8.1-py313-24.04 vastai/pytorch:2.7.1-cuda-12.8.1-py313-24.04

# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.6.3-py310-22.04 vastai/pytorch:2.8.0-cuda-12.6.3-py310-22.04 vastai/pytorch:2.8.0-cuda-12.6.3-22.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.6.3-py311-22.04 vastai/pytorch:2.8.0-cuda-12.6.3-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.6.3-py312-22.04 vastai/pytorch:2.8.0-cuda-12.6.3-py312-22.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.6.3-py313-22.04 vastai/pytorch:2.8.0-cuda-12.6.3-py313-22.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.8.1-py310-22.04 vastai/pytorch:2.8.0-cuda-12.8.1-py310-22.04 vastai/pytorch:2.8.0-cuda-12.8.1-22.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.8.1-py311-22.04 vastai/pytorch:2.8.0-cuda-12.8.1-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.8.1-py312-22.04 vastai/pytorch:2.8.0-cuda-12.8.1-py312-22.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.8.1-py313-22.04 vastai/pytorch:2.8.0-cuda-12.8.1-py313-22.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.9.1-py310-22.04 vastai/pytorch:2.8.0-cuda-12.9.1-py310-22.04 vastai/pytorch:2.8.0-cuda-12.9.1-22.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.9.1-py311-22.04 vastai/pytorch:2.8.0-cuda-12.9.1-py311-22.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.9.1-py312-22.04 vastai/pytorch:2.8.0-cuda-12.9.1-py312-22.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.9.1-py313-22.04 vastai/pytorch:2.8.0-cuda-12.9.1-py313-22.04

# ../../retag.sh robatvastai/pytorch:2.8.0-cu129-cuda-12.9-mini-py310 vastai/pytorch:2.8.0-cu128-cuda-12.9-mini-py310
# ../../retag.sh robatvastai/pytorch:2.8.0-cu129-cuda-12.9-mini-py311 vastai/pytorch:2.8.0-cu128-cuda-12.9-mini-py311
# ../../retag.sh robatvastai/pytorch:2.8.0-cu129-cuda-12.9-mini-py312 vastai/pytorch:2.8.0-cu128-cuda-12.9-mini-py312

# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.6.3-py310-24.04 vastai/pytorch:2.8.0-cuda-12.6.3-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.6.3-py311-24.04 vastai/pytorch:2.8.0-cuda-12.6.3-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.6.3-py312-24.04 vastai/pytorch:2.8.0-cuda-12.6.3-py312-24.04 vastai/pytorch:2.8.0-cuda-12.6.3-24.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.6.3-py313-24.04 vastai/pytorch:2.8.0-cuda-12.6.3-py313-24.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.8.1-py310-24.04 vastai/pytorch:2.8.0-cuda-12.8.1-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.8.1-py311-24.04 vastai/pytorch:2.8.0-cuda-12.8.1-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.8.1-py312-24.04 vastai/pytorch:2.8.0-cuda-12.8.1-py312-24.04 vastai/pytorch:2.8.0-cuda-12.8.1-24.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.8.1-py313-24.04 vastai/pytorch:2.8.0-cuda-12.8.1-py313-24.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.9.1-py310-24.04 vastai/pytorch:2.8.0-cuda-12.9.1-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.9.1-py311-24.04 vastai/pytorch:2.8.0-cuda-12.9.1-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.9.1-py312-24.04 vastai/pytorch:2.8.0-cuda-12.9.1-py312-24.04 vastai/pytorch:2.8.0-cuda-12.9.1-24.04
# ../../retag.sh robatvastai/pytorch:2.8.0-cuda-12.9.1-py313-24.04 vastai/pytorch:2.8.0-cuda-12.9.1-py313-24.04

# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-12.6.3-py310-24.04 vastai/pytorch:2.9.0-cuda-12.6.3-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-12.6.3-py311-24.04 vastai/pytorch:2.9.0-cuda-12.6.3-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-12.6.3-py312-24.04 vastai/pytorch:2.9.0-cuda-12.6.3-py312-24.04 vastai/pytorch:2.9.0-cuda-12.6.3-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-12.6.3-py313-24.04 vastai/pytorch:2.9.0-cuda-12.6.3-py313-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-12.6.3-py314-24.04 vastai/pytorch:2.9.0-cuda-12.6.3-py314-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-12.8.1-py310-24.04 vastai/pytorch:2.9.0-cuda-12.8.1-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-12.8.1-py311-24.04 vastai/pytorch:2.9.0-cuda-12.8.1-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-12.8.1-py312-24.04 vastai/pytorch:2.9.0-cuda-12.8.1-py312-24.04 vastai/pytorch:2.9.0-cuda-12.8.1-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-12.8.1-py313-24.04 vastai/pytorch:2.9.0-cuda-12.8.1-py313-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-12.8.1-py314-24.04 vastai/pytorch:2.9.0-cuda-12.8.1-py314-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-13.0.2-py310-24.04 vastai/pytorch:2.9.0-cuda-13.0.2-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-13.0.2-py311-24.04 vastai/pytorch:2.9.0-cuda-13.0.2-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-13.0.2-py312-24.04 vastai/pytorch:2.9.0-cuda-13.0.2-py312-24.04 vastai/pytorch:2.9.0-cuda-13.0.2-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-13.0.2-py313-24.04 vastai/pytorch:2.9.0-cuda-13.0.2-py313-24.04
# ../../retag.sh robatvastai/pytorch:2.9.0-cuda-13.0.2-py314-24.04 vastai/pytorch:2.9.0-cuda-13.0.2-py314-24.04

# ../../retag.sh robatvastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py310 vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py310
# ../../retag.sh robatvastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py311 vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py311
# ../../retag.sh robatvastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312 vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312

# ../../retag.sh robatvastai/pytorch:2.9.1-cu130-cuda-13.1-mini-py310 vastai/pytorch:2.9.1-cu130-cuda-13.1-mini-py310
# ../../retag.sh robatvastai/pytorch:2.9.1-cu130-cuda-13.1-mini-py311 vastai/pytorch:2.9.1-cu130-cuda-13.1-mini-py311
# ../../retag.sh robatvastai/pytorch:2.9.1-cu130-cuda-13.1-mini-py312 vastai/pytorch:2.9.1-cu130-cuda-13.1-mini-py312

# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-12.6.3-py310-24.04 vastai/pytorch:2.9.1-cuda-12.6.3-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-12.6.3-py311-24.04 vastai/pytorch:2.9.1-cuda-12.6.3-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-12.6.3-py312-24.04 vastai/pytorch:2.9.1-cuda-12.6.3-py312-24.04 vastai/pytorch:2.9.1-cuda-12.6.3-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-12.6.3-py313-24.04 vastai/pytorch:2.9.1-cuda-12.6.3-py313-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-12.6.3-py314-24.04 vastai/pytorch:2.9.1-cuda-12.6.3-py314-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-12.8.1-py310-24.04 vastai/pytorch:2.9.1-cuda-12.8.1-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-12.8.1-py311-24.04 vastai/pytorch:2.9.1-cuda-12.8.1-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-12.8.1-py312-24.04 vastai/pytorch:2.9.1-cuda-12.8.1-py312-24.04 vastai/pytorch:2.9.1-cuda-12.8.1-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-12.8.1-py313-24.04 vastai/pytorch:2.9.1-cuda-12.8.1-py313-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-12.8.1-py314-24.04 vastai/pytorch:2.9.1-cuda-12.8.1-py314-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-13.0.2-py310-24.04 vastai/pytorch:2.9.1-cuda-13.0.2-py310-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-13.0.2-py311-24.04 vastai/pytorch:2.9.1-cuda-13.0.2-py311-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-13.0.2-py312-24.04 vastai/pytorch:2.9.1-cuda-13.0.2-py312-24.04 vastai/pytorch:2.9.1-cuda-13.0.2-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-13.0.2-py313-24.04 vastai/pytorch:2.9.1-cuda-13.0.2-py313-24.04
# ../../retag.sh robatvastai/pytorch:2.9.1-cuda-13.0.2-py314-24.04 vastai/pytorch:2.9.1-cuda-13.0.2-py314-24.04

../../retag.sh robatvastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py310 vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py310
../../retag.sh robatvastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py311 vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py311
../../retag.sh robatvastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312 vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312

../../retag.sh robatvastai/pytorch:2.10.0-cu130-cuda-13.1-mini-py310 vastai/pytorch:2.10.0-cu130-cuda-13.1-mini-py310
../../retag.sh robatvastai/pytorch:2.10.0-cu130-cuda-13.1-mini-py311 vastai/pytorch:2.10.0-cu130-cuda-13.1-mini-py311
../../retag.sh robatvastai/pytorch:2.10.0-cu130-cuda-13.1-mini-py312 vastai/pytorch:2.10.0-cu130-cuda-13.1-mini-py312

../../retag.sh robatvastai/pytorch:2.10.0-cuda-12.6.3-py310-24.04 vastai/pytorch:2.10.0-cuda-12.6.3-py310-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-12.6.3-py311-24.04 vastai/pytorch:2.10.0-cuda-12.6.3-py311-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-12.6.3-py312-24.04 vastai/pytorch:2.10.0-cuda-12.6.3-py312-24.04 vastai/pytorch:2.10.0-cuda-12.6.3-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-12.6.3-py313-24.04 vastai/pytorch:2.10.0-cuda-12.6.3-py313-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-12.6.3-py314-24.04 vastai/pytorch:2.10.0-cuda-12.6.3-py314-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-12.8.1-py310-24.04 vastai/pytorch:2.10.0-cuda-12.8.1-py310-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-12.8.1-py311-24.04 vastai/pytorch:2.10.0-cuda-12.8.1-py311-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-12.8.1-py312-24.04 vastai/pytorch:2.10.0-cuda-12.8.1-py312-24.04 vastai/pytorch:2.10.0-cuda-12.8.1-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-12.8.1-py313-24.04 vastai/pytorch:2.10.0-cuda-12.8.1-py313-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-12.8.1-py314-24.04 vastai/pytorch:2.10.0-cuda-12.8.1-py314-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-13.0.2-py310-24.04 vastai/pytorch:2.10.0-cuda-13.0.2-py310-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-13.0.2-py311-24.04 vastai/pytorch:2.10.0-cuda-13.0.2-py311-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-13.0.2-py312-24.04 vastai/pytorch:2.10.0-cuda-13.0.2-py312-24.04 vastai/pytorch:2.10.0-cuda-13.0.2-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-13.0.2-py313-24.04 vastai/pytorch:2.10.0-cuda-13.0.2-py313-24.04
../../retag.sh robatvastai/pytorch:2.10.0-cuda-13.0.2-py314-24.04 vastai/pytorch:2.10.0-cuda-13.0.2-py314-24.04

../../retag.sh vastai/pytorch:2.6.0-cuda-11.8.0-py310-22.04 vastai/pytorch:cuda-11.8.0-auto
# CUDA 12.1 - 12.6 sharing same image - Forward or Minor version compatibility to handle
../../retag.sh vastai/pytorch:2.10.0-cuda-12.6.3-py312-24.04 vastai/pytorch:cuda-12.1.1-auto
../../retag.sh vastai/pytorch:2.10.0-cuda-12.6.3-py312-24.04 vastai/pytorch:cuda-12.4.1-auto
../../retag.sh vastai/pytorch:2.10.0-cuda-12.6.3-py312-24.04 vastai/pytorch:cuda-12.6.3-auto
# CUDA 12.8 and 12.9 sharing an image.  PyTorch was only briefly built against 12.9. 12.8 still receives updates  
../../retag.sh vastai/pytorch:2.10.0-cuda-12.8.1-py312-24.04 vastai/pytorch:cuda-12.8.1-auto
../../retag.sh vastai/pytorch:2.10.0-cuda-12.8.1-py312-24.04 vastai/pytorch:cuda-12.9.1-auto
../../retag.sh vastai/pytorch:2.10.0-cuda-13.0.2-py312-24.04 vastai/pytorch:cuda-13.0.2-auto
