ARG VAST_BASE=vastai/base-image:stock-ubuntu24.04-py312-2026-10-23
ARG UNSLOTH_BASE=unsloth/unsloth:nightly-2026.10.18
FROM ${VAST_BASE} AS vast_base_image
FROM ${UNSLOTH_BASE} AS unsloth_build

# The image tag carries the CUDA version the workflow passes; fail rather than publish a
# tag that names the wrong toolkit when the upstream moves to a new CUDA.
ARG UNSLOTH_CUDA=12.8
RUN [[ "${CUDA_VERSION}" == "${UNSLOTH_CUDA}" || "${CUDA_VERSION}" == "${UNSLOTH_CUDA}."* ]] || \
    { echo "upstream CUDA_VERSION=${CUDA_VERSION}, workflow says ${UNSLOTH_CUDA}"; exit 1; }

LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Unsloth suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

SHELL ["/bin/bash", "-c"]
WORKDIR /
# An external image FROMs the upstream, so it does NOT inherit the base image's ENV. TCLLIBPATH
# is REQUIRED (L055): without it the base pty helper's `unbuffer` (Tcl/Expect) fails early in boot
# and the launch cascade dies. The /opt/sys-venv/shim PATH entry (convert-non-vast-image.sh puts
# supervisord/jupyter there) matches vllm/sglang/ollama, but vast_boot.d/10-prep-env.sh also adds
# it at runtime — belt-and-suspenders, so it's not gated (vllm-omni omits it and is fine).
ENV DATA_DIRECTORY=/workspace \
    WORKSPACE=/workspace \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    UV_LINK_MODE=copy \
    TCLLIBPATH=/usr/lib/tcltk/default \
    PATH=/opt/instance-tools/bin:/opt/sys-venv/shim:$PATH

# NOTE: the `base_image_source` stage is supplied at build time via
# `--build-context base_image_source=<repo root>` (see .github/AGENTS.md).
COPY --from=base_image_source /ROOT /
COPY --from=base_image_source /portal-aio /opt/portal-aio
COPY --from=vast_base_image /opt/portal-aio/caddy_manager/caddy /opt/portal-aio/caddy_manager/caddy
# The interpreter that runs the upstream's engine, and the modules that prove it (ADR 0048,
# L101). The convert script builds /venv/main as a per-file mirror of its site-packages so uv
# sees the engine as installed; it refuses to guess. Use `none` if there is no Python engine
# (then delete VAST_ENGINE_IMPORT and use a plain `RUN env-hash > /.env_hash` below).
ENV VAST_ENGINE_PYTHON=/opt/unsloth-venv/bin/python
# Not `unsloth`: it refuses to import without a GPU, and the build runner has none. vllm and
# trl import on CPU and come from the same upstream venv; tests/unsloth.d imports unsloth on
# the GPU.
ENV VAST_ENGINE_IMPORT="vllm trl"
COPY --from=base_image_source tools/convert-non-vast-image.sh /tmp/convert-non-vast-image.sh
RUN set -euo pipefail; \
    chmod +x /tmp/convert-non-vast-image.sh; \
    /tmp/convert-non-vast-image.sh; \
    rm /tmp/convert-non-vast-image.sh

COPY ./ROOT /

# The upstream's supervisord.conf (Unsloth Studio, its own Jupyter on 8888, sshd) is
# overwritten by the base ROOT above, so the Vast stack runs instead: Jupyter on 8080
# behind Caddy, SSH from the base. GRPO files come in with ./ROOT under
# /opt/workspace-internal and sync to $WORKSPACE on first boot.
RUN set -euo pipefail; \
    /venv/main/bin/python -m ipykernel install --name "python3" --display-name "Python 3 (Unsloth)"; \
    chmod +x /opt/supervisor-scripts/grpo-autorun.sh

RUN venv-mirror verify --build --engine-import "${VAST_ENGINE_IMPORT}" && env-hash > /.env_hash
ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
