ARG PYTORCH_BASE=vastai/pytorch:2.9.1-cu128-cuda-12.9-mini-py312-2026-09-01

FROM ${PYTORCH_BASE}

# Maintainer details
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Ostris AI Toolkit image suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"

# Copy Supervisor configuration and startup scripts
COPY ./ROOT /

# Required or we will not build
ARG AI_TOOLKIT_REPO=https://github.com/ostris/ai-toolkit
ARG AI_TOOLKIT_REF=6870ab4
RUN \
    set -euo pipefail && \
    [[ -n "${AI_TOOLKIT_REF}" ]] || { echo "Must specify AI_TOOLKIT_REF"; exit 1; } && \
    . /venv/main/bin/activate && \
    . /opt/nvm/nvm.sh && \
    # Snapshot the base's full torch ecosystem (torch + torchvision + torchaudio + torchcodec).
    # The torch-only check we used to do here was the gap that let silent
    # torchcodec ABI clobbers slip through — see the strip comment below.
    torch_versions_pre="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    cd /opt/workspace-internal && \
    git clone "${AI_TOOLKIT_REPO}" && \
    cd $(basename ${AI_TOOLKIT_REPO}) && \
    git checkout "${AI_TOOLKIT_REF}" && \
    # Fixes known issue with toolkit 6870ab4
    uv pip install --no-cache-dir timm==1.0.22 && \
    # Strip all base-provided torch ecosystem pins from upstream
    # requirements so the pytorch base remains the single source of
    # truth for those versions. ai-toolkit currently pins torchcodec
    # in requirements_base.txt; future upstream changes might add
    # torch/torchvision/torchaudio pins too. Without this defensive
    # strip, any version skew between an upstream pin and the base
    # silently clobbers the base — torchcodec's PyPI metadata declares
    # no torch requirement (no resolver constraint to catch the
    # mismatch). The diff-based torch ecosystem assertion at the end of
    # this RUN catches drift regardless. torchao is NOT stripped here:
    # it isn't in the pytorch base, so ai-toolkit's explicit pin is
    # what installs it.
    sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' requirements_base.txt && \
    uv pip install --no-cache-dir -r requirements.txt && \
    cd ui && \
    # AI Toolkit's UI binds EVERY interface. On the current tree `cron/fileServer.ts`
    # ends in `server.listen(publicPort)` with no host argument, which is 0.0.0.0; the
    # older shape launches `next start --port 8675`, equally wide. Note the same file
    # already pins its INTERNAL Next.js upstream with `--hostname 127.0.0.1` — only the
    # public half is exposed. Everything here sits behind Caddy, so the public listener
    # belongs on loopback (L091).
    #
    # Detect the shape rather than assume it, and make an unrecognised one FATAL. BOTH
    # images resolve the upstream ref in CI (each workflow resolves HEAD and passes
    # <APP>_REF), so neither is anchored to a known shape and either can meet either one
    # on any given day. The ARG default below is a local-build fallback that CI never
    # uses; it is stale enough to describe a DIFFERENT launch shape, which is precisely
    # why the shape must be read from the tree rather than inferred from the ref. A sed
    # that silently matched nothing would ship the public bind, which is the failure this
    # guard exists to prevent.
    #
    # Patched BEFORE `npm run build`, because tsc compiles cron/*.ts into dist/cron/.
    if [ -f cron/fileServer.ts ]; then \
        sed -i -E "s/server\.listen\(publicPort\)/server.listen(publicPort, '127.0.0.1')/" cron/fileServer.ts && \
        grep -qE "listen\(publicPort, ['\"]127\.0\.0\.1['\"]\)" cron/fileServer.ts || \
            { echo "FATAL: fileServer.ts public listen could not be pinned to 127.0.0.1 loopback — upstream changed shape (L091)"; exit 1; }; \
    elif grep -q 'next start' package.json; then \
        grep -q 'next start --hostname 127.0.0.1' package.json || \
            sed -i -E 's/next start/next start --hostname 127.0.0.1/' package.json; \
        grep -q 'next start --hostname 127.0.0.1' package.json || \
            { echo "FATAL: next start could not be pinned to 127.0.0.1 loopback (L091)"; exit 1; }; \
    else \
        echo "FATAL: unrecognised AI Toolkit UI launch shape at ${AI_TOOLKIT_REF} — refusing to ship a bind we cannot pin to loopback (L091)"; exit 1; \
    fi && \
    npm install && \
    npm run update_db && \
    npm run build && \
    # Verify the torch ecosystem (torch + torchvision + torchaudio + torchcodec)
    # versions are unchanged. Catches silent ABI clobbers that the
    # torch-only check used to miss.
    torch_versions_post="$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)" && \
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem version drift:"; diff <(echo "$torch_versions_pre") <(echo "$torch_versions_post"); exit 1; }

# Defend against environment clashes when syncing to volume
RUN \
    set -euo pipefail && \
    env-hash > /.env_hash
