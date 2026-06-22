"""Deterministic generator for the mechanical ~80% of a new image.

Emits Dockerfile + ROOT/ overlay + README + CI skeleton per class, with fenced
fill-markers (`>>> FILL: ... <<<`) for the judgment residue a human/LLM completes.
Output is designed to pass the linter by construction (see test_generate round-trip).
Templating is stdlib @@TOKEN@@ substitution (no Jinja2) to keep deps minimal and
avoid brace clashes with shell `${...}`.
"""
from __future__ import annotations
from pathlib import Path

CLASSES = ("derivative", "pytorch-nested", "external")
_CLASS_DIR = {
    "derivative": "derivatives",
    "pytorch-nested": "derivatives/pytorch/derivatives",
    "external": "external",
}
_CAP_NN = {"derivative": "30", "pytorch-nested": "50", "external": "50"}

_LABELS = '''LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="@@LABEL@@ suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"'''

_TORCH_SNAP = (
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
    . /venv/main/bin/activate; \\
    torch_versions_pre=@@SNAP@@; \\
    : '>>> FILL: install @@NAME@@ — git clone @@NAME_UPPER@@_REF, strip torch pins, uv pip install <<<'; \\
    torch_versions_post=@@SNAP@@; \\
    [[ "$torch_versions_pre" = "$torch_versions_post" ]] || { echo "torch ecosystem drift for @@NAME@@"; exit 1; }

RUN env-hash > /.env_hash
'''

_DF_EXTERNAL = '''ARG VAST_BASE=vastai/base-image:CHANGEME
ARG @@NAME_UPPER@@_BASE=@@UPSTREAM@@
FROM ${VAST_BASE} AS vast_base_image
FROM ${@@NAME_UPPER@@_BASE}

@@LABELS@@

ENV DATA_DIRECTORY=/workspace \\
    WORKSPACE=/workspace \\
    PATH=/opt/instance-tools/bin:$PATH

COPY --from=base_image_source /ROOT /
COPY --from=base_image_source /portal-aio /opt/portal-aio
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

_SUPERVISOR_SH = '''#!/bin/bash
# Port hint: PORTAL_CONFIG entry "localhost:@@PORT@@:@@EXTPORT@@:/:@@LABEL@@"
# (+10000 external offset is the common convention, not a hard rule.)
utils="/opt/supervisor-scripts/utils"
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh"

exit_portal "@@LABEL@@"
. /venv/main/bin/activate
while [ -f "/.provisioning" ]; do sleep 5; done
cd "${WORKSPACE}/@@NAME@@" 2>/dev/null || cd "${WORKSPACE}"

# >>> FILL: launch command for @@NAME@@ <<<
exec true
'''

_CONF = '''[program:@@NAME@@]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/@@NAME@@.sh
autostart=true
autorestart=unexpected
stdout_logfile=/dev/stdout
redirect_stderr=true
'''

_CAP = '''# Capability manifest fragment for @@NAME@@
name: @@NAME@@
readme: README.md
# >>> FILL: preinstalled, python_environments, etc. <<<
'''

_AGENT = '''# @@LABEL@@

> FILL: how an AI agent should operate @@NAME@@ on this image — entrypoints,
> ports, where models/data live, common tasks.
'''

_README = '''# @@LABEL@@

Vast.ai image for @@NAME@@.

> FILL: description, usage, ports, provisioning notes.
'''

_BOOT_ENV = '''#!/bin/bash
if [[ -z $PORTAL_CONFIG ]]; then
  export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:@@PORT@@:@@EXTPORT@@:/:@@LABEL@@"
fi
'''

_WORKFLOW = '''# >>> FILL: CI workflow skeleton for @@NAME@@ — complete per .github/AGENTS.md.
# Real shape is the 5-job pipeline (preflight -> build -> merge-manifests ->
# collect-tags -> notify); job-shape is NOT linted, so review against a sibling
# build-*.yml before relying on this. <<<
name: Build @@NAME@@
on:
  workflow_dispatch:
  schedule:
    - cron: "0 0,12 * * *"
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
               PORT=str(port), EXTPORT=str(port + 10000), UPSTREAM=upstream or "",
               LABELS=_render(_LABELS, LABEL=label), SNAP=_TORCH_SNAP)

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
        d / "README.md": _render(_README, **sub),
        d / "README.template.md": _render(_README, **sub),
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
