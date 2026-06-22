"""Deterministic generator for the mechanical ~80% of a new image.

Templates are modelled on the REAL repo images (comfyui, vllm), not invented.
Emits Dockerfile + ROOT/ overlay + README + CI skeleton per class, with fenced
fill-markers (`>>> FILL: ... <<<`, `CHANGEME`, `FIXME:`) for the judgment residue.
A generated image passes the STRUCTURAL linter but L040 flags it as an incomplete
skeleton until the markers are resolved (so "lint clean" never means "ready" by
accident). All placeholders use a single marker dialect (`>>> FILL` / `CHANGEME` /
`CHANGEPORT`) so L040 cannot have dialect gaps; residual stub lines carry the
marker themselves so they can't be left behind silently. Templating is stdlib
@@TOKEN@@ substitution (no Jinja2) to keep deps minimal and avoid brace clashes
with shell `${...}`.
"""
from __future__ import annotations
from pathlib import Path

CLASSES = ("derivative", "pytorch-nested", "external")
_CLASS_DIR = {
    "derivative": "derivatives",
    "pytorch-nested": "derivatives/pytorch/derivatives",
    "external": "external",
}
_CAP_NN = {"derivative": "30", "pytorch-nested": "50", "external": "30"}  # external grafts onto base

_LABELS = '''LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="@@LABEL@@ suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"'''

_SNAP = (
    '''"$({ pip list --format=freeze 2>/dev/null | grep -E '^(torch|torchvision|torchaudio|torchcodec)==' || :; } | sort)"'''
)

_DF_DERIVATIVE = '''ARG VAST_BASE=vastai/base-image:CHANGEME
FROM ${VAST_BASE}

@@LABELS@@

COPY ./ROOT /

RUN set -euo pipefail; \\
    . /venv/main/bin/activate; \\
    : '>>> FILL: install @@NAME@@ here (apt/uv pip install, build, etc.) <<<'

RUN env-hash > /.env_hash
'''

_DF_PYTORCH = '''ARG PYTORCH_BASE=vastai/pytorch:CHANGEME
FROM ${PYTORCH_BASE}

@@LABELS@@

COPY ./ROOT /

ARG @@NAME_UPPER@@_REF
RUN set -euo pipefail; \\
    [[ -n "${@@NAME_UPPER@@_REF}" ]] || { echo "Must specify @@NAME_UPPER@@_REF"; exit 1; }; \\
    . /venv/main/bin/activate; \\
    torch_versions_pre=@@SNAP@@; \\
    : '>>> FILL: install @@NAME@@ HERE, in THIS SAME RUN, between the two snapshots — git clone @@NAME_UPPER@@_REF, strip torch pins, uv pip install. Do NOT move this into a separate RUN: that makes the drift guard a no-op the linter cannot catch. <<<'; \\
    torch_versions_post=@@SNAP@@; \\
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem drift for @@NAME@@"; exit 1; }

RUN env-hash > /.env_hash
'''

_DF_EXTERNAL = '''ARG VAST_BASE=vastai/base-image:CHANGEME
ARG @@NAME_UPPER@@_BASE=@@UPSTREAM@@
FROM ${VAST_BASE} AS vast_base_image
FROM ${@@NAME_UPPER@@_BASE} AS @@NAME@@_build

@@LABELS@@

SHELL ["/bin/bash", "-c"]
WORKDIR /
ENV DATA_DIRECTORY=/workspace \\
    WORKSPACE=/workspace \\
    DEBIAN_FRONTEND=noninteractive \\
    PYTHONUNBUFFERED=1 \\
    PIP_BREAK_SYSTEM_PACKAGES=1 \\
    UV_LINK_MODE=copy \\
    PATH=/opt/instance-tools/bin:$PATH

# NOTE: the `base_image_source` stage is supplied at build time via
# `--build-context base_image_source=<repo root>` (see .github/AGENTS.md).
COPY --from=base_image_source /ROOT /
COPY --from=base_image_source /portal-aio /opt/portal-aio
COPY --from=vast_base_image /opt/portal-aio/caddy_manager/caddy /opt/portal-aio/caddy_manager/caddy
COPY --from=base_image_source tools/convert-non-vast-image.sh /tmp/convert-non-vast-image.sh
RUN set -euo pipefail; \\
    chmod +x /tmp/convert-non-vast-image.sh; \\
    /tmp/convert-non-vast-image.sh; \\
    rm /tmp/convert-non-vast-image.sh

COPY ./ROOT /

RUN set -euo pipefail; \\
    : '>>> FILL: install/configure @@NAME@@ on top of the upstream image <<<'

RUN env-hash > /.env_hash
ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]
CMD []
'''

# Modelled on the real comfyui.sh: utils sourced in order; exit_portal.sh is SOURCED
# WITH the label as $1, inside a SERVERLESS guard (it is NOT a function).
_SUPERVISOR_SH = '''#!/bin/bash

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

# Serverless: skip the portal-config gate when running serverless
if [[ "${SERVERLESS:-false}" != "true" ]]; then
    . "${utils}/exit_portal.sh" "@@LABEL@@"
fi

[[ -f /venv/main/bin/activate ]] && . /venv/main/bin/activate

# Wait for provisioning to complete before starting
while [ -f "/.provisioning" ]; do
    echo "$PROC_NAME startup paused until provisioning completes (/.provisioning present)"
    sleep 5
done

cd "${WORKSPACE}/@@NAME@@" 2>/dev/null || cd "${WORKSPACE}"

# >>> FILL: launch @@NAME@@ using `pty` (e.g. `pty python main.py ...`) <<<
exit 1  # >>> FILL: remove this line once the launch command above is in place <<<
'''

_CONF = '''[program:@@NAME@@]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/@@NAME@@.sh
autostart=true
autorestart=unexpected
stdout_logfile=/dev/stdout
redirect_stderr=true
'''

# Modelled on real 50-comfyui.yaml: top-level `image:` mapping overriding the base block.
_CAP = '''# @@LABEL@@ — image identity (overrides the base image block).
image:
  name: @@LABEL@@
  readme: ">>> FILL: GitHub URL to this image's directory <<<"
  preinstalled: ">>> FILL: e.g. PyTorch + @@LABEL@@ <<<"
'''

_AGENT = '''# @@LABEL@@

>>> FILL: how an AI agent should operate @@NAME@@ on this image — entrypoints,
ports, where models/data live, common tasks. <<<
'''

# README.md = developer docs; README.template.md = Vast.ai marketplace listing.
# These are DISTINCT artifacts (see real vllm/README.md vs README.template.md).
_README_DEV = '''# @@LABEL@@ Image

A Vast.ai image for @@NAME@@. Includes the Instance Portal, Supervisor process
management, and other conveniences from the Vast.ai
[base image](https://github.com/vast-ai/base-image).

>>> FILL: how this image works, available tags, environment variables. <<<
'''

_README_TEMPLATE = '''# @@LABEL@@
> **[Create an Instance](https://cloud.vast.ai/?ref_id=62897&creator_id=62897&name=@@NAME@@)**

## What is this template?

>>> FILL: marketplace description of @@LABEL@@ for end users — what it does and why. <<<
'''

# external only: PORTAL_CONFIG ports are NOT internal+10000 by rule (invariants §3),
# and must match the app's real bind port (§4) — so they are FILL markers, not baked.
_BOOT_ENV = '''#!/bin/bash
if [[ -z $PORTAL_CONFIG ]]; then
  # >>> FILL: real internal:external ports + path for @@LABEL@@ (ports must match the app's bind port) <<<
  export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:CHANGEPORT:CHANGEPORT:/:@@LABEL@@"
fi
'''

# CI job-shape is NOT linted and is complex (5-job pipeline); schedules are staggered,
# NOT uniform (invariants §3) — so no cron is baked in.
_WORKFLOW = '''# >>> FILL: CI workflow for @@NAME@@ — complete per .github/AGENTS.md. Real shape is the
# 5-job pipeline (preflight -> build -> merge-manifests -> collect-tags -> notify);
# review against a sibling build-*.yml. Set a STAGGERED schedule, not a uniform cron. <<<
name: Build @@NAME@@
on:
  workflow_dispatch:
jobs:
  preflight:
    runs-on: ubuntu-latest
    steps:
      - run: 'echo ">>> FILL <<<"'
'''


def _render(tmpl: str, **kw: str) -> str:
    for k, v in kw.items():
        tmpl = tmpl.replace(f"@@{k}@@", v)
    return tmpl


def generate(repo: Path, *, name: str, cls: str, label: str, port: int,
             upstream: str | None = None) -> Path:
    """Write the file set for a new image; returns its directory. Human picks `cls`."""
    if cls not in CLASSES:
        raise ValueError(f"unknown class {cls!r}; expected one of {CLASSES}")
    if cls == "external" and not upstream:
        raise ValueError("external images require --upstream <image:tag>")

    sub = dict(NAME=name, NAME_UPPER=name.upper().replace("-", "_"), LABEL=label,
               PORT=str(port), UPSTREAM=upstream or "",
               LABELS=_render(_LABELS, LABEL=label), SNAP=_SNAP)
    df_tmpl = {"derivative": _DF_DERIVATIVE, "pytorch-nested": _DF_PYTORCH,
               "external": _DF_EXTERNAL}[cls]

    d = repo / _CLASS_DIR[cls] / name
    root = d / "ROOT"
    (root / "opt/supervisor-scripts").mkdir(parents=True, exist_ok=True)
    (root / "etc/supervisor/conf.d").mkdir(parents=True, exist_ok=True)
    (root / "etc/vast_capabilities.d").mkdir(parents=True, exist_ok=True)
    (root / "etc/vast_agents").mkdir(parents=True, exist_ok=True)

    files = {
        d / "Dockerfile": _render(df_tmpl, **sub),
        d / "README.md": _render(_README_DEV, **sub),
        d / "README.template.md": _render(_README_TEMPLATE, **sub),
        root / "opt/supervisor-scripts" / f"{name}.sh": _render(_SUPERVISOR_SH, **sub),
        root / "etc/supervisor/conf.d" / f"{name}.conf": _render(_CONF, **sub),
        root / "etc/vast_capabilities.d" / f"{_CAP_NN[cls]}-{name}.yaml": _render(_CAP, **sub),
        root / "etc/vast_agents" / f"{name}.md": _render(_AGENT, **sub),
    }
    if cls == "external":
        (root / "etc/vast_boot.d").mkdir(parents=True, exist_ok=True)
        files[root / "etc/vast_boot.d" / f"05-{name}-env.sh"] = _render(_BOOT_ENV, **sub)

    wf = repo / ".github/workflows" / f"build-{name}.yml"
    wf.parent.mkdir(parents=True, exist_ok=True)
    files[wf] = _render(_WORKFLOW, **sub)

    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    return d
