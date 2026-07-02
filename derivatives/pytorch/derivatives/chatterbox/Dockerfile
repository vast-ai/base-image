ARG PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312-2026-06-15
FROM ${PYTORCH_BASE}

LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Chatterbox suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

COPY ./ROOT /

ARG CHATTERBOX_REF
RUN set -euo pipefail; \
    [[ -n "${CHATTERBOX_REF}" ]] || { echo "Must specify CHATTERBOX_REF"; exit 1; }; \
    . /venv/main/bin/activate; \
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)"; \
    apt-get update && apt-get install -y --no-install-recommends libsndfile1 ffmpeg && rm -rf /var/lib/apt/lists/*; \
    git clone https://github.com/devnen/Chatterbox-TTS-Server.git /opt/workspace-internal/chatterbox; \
    cd /opt/workspace-internal/chatterbox && git checkout "${CHATTERBOX_REF}"; \
    sed -i -E '/^--extra-index-url .*download\.pytorch\.org/d; /^torch(vision|audio|codec)?[=<>! ]/d' requirements-nvidia-cu128.txt; \
    uv pip install --no-cache-dir -r requirements-nvidia-cu128.txt; \
    uv pip install --no-cache-dir --no-deps git+https://github.com/devnen/chatterbox-v2.git@df73ac81662c9354e0d408832a876ddd72a42480 s3tokenizer==0.3.0 onnx==1.16.0; \
    uv pip install --no-cache-dir "protobuf>=4.25.0"; \
    sed -i -E 's/^([[:space:]]*host:).*/\1 127.0.0.1/' /opt/workspace-internal/chatterbox/config.yaml; \
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)"; \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem drift for chatterbox"; exit 1; }

RUN env-hash > /.env_hash
