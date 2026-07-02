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
> **[Create an Instance](https://cloud.vast.ai/?ref_id=525202&creator_id=525202&name=@@NAME@@)**

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
_WORKFLOW = '''# Build @@LABEL@@ image — scheduled + manual workflow_dispatch.
# Required repository secrets: DOCKERHUB_USERNAME, DOCKERHUB_TOKEN,
#   DOCKERHUB_NAMESPACE (prod, final multi-arch index), DOCKERHUB_NAMESPACE_STAGING
#   (per-arch build artifacts). Optional: SLACK_WEBHOOK_URL.
#
# imagegen scaffolds the standard 5-job pipeline (preflight -> build ->
# merge-manifests -> collect-tags -> notify) with the DockerHub secret-refs, the
# production approval gate, and Slack notify already wired. CI job-shape is NOT linted,
# so before opening a PR: review this against the closest sibling build-*.yml and resolve
# every CHANGEME / >>> FILL. The per-image variation is concentrated in the preflight
# release check (which action) and the base-image matrix / tag derivation.
name: Build @@LABEL@@ Image

on:
  schedule:
    # Staggered schedule — pick a unique offset (avoid a time shared with siblings),
    # or drop the schedule entirely until the image is proven.
    - cron: 'CHANGEME'
  workflow_dispatch:
    inputs:
      @@NAME_UPPER@@_REF:
        description: "@@LABEL@@ upstream ref/version - empty for latest"
        required: false
      DOCKERHUB_REPO:
        description: "Push to <namespace>/<repo>"
        required: true
        default: "@@NAME@@"
      CUSTOM_IMAGE_TAG:
        description: "Custom tag (auto by default)"
        required: false

env:
  DEFAULT_DOCKERHUB_REPO: "@@NAME@@"
  RELEASE_AGE_THRESHOLD: 43200   # 12h

jobs:
  preflight:
    runs-on: ubuntu-latest
    outputs:
      should-run: ${{ steps.decision.outputs.should-run }}
      resolved-ref: ${{ steps.release-check.outputs.resolved-ref }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Check for required secrets
        id: secrets
        run: |
          missing=()
          [[ -z "${{ secrets.DOCKERHUB_USERNAME }}" ]] && missing+=("DOCKERHUB_USERNAME")
          [[ -z "${{ secrets.DOCKERHUB_TOKEN }}" ]] && missing+=("DOCKERHUB_TOKEN")
          [[ -z "${{ secrets.DOCKERHUB_NAMESPACE }}" ]] && missing+=("DOCKERHUB_NAMESPACE")
          [[ -z "${{ secrets.DOCKERHUB_NAMESPACE_STAGING }}" ]] && missing+=("DOCKERHUB_NAMESPACE_STAGING")
          if [[ ${#missing[@]} -eq 0 ]]; then
            echo "available=true" >> $GITHUB_OUTPUT
          else
            echo "available=false" >> $GITHUB_OUTPUT
            echo "::notice::Skipping - missing secrets: ${missing[*]}"
          fi

      # Plug in the preflight check for this image's upstream — one of:
      #   check-github-release (GitHub repo) | check-pypi-release (PyPI package) |
      #   check-ghcr-release (GHCR image)    | check-dockerhub-release (DockerHub image).
      # It must emit `new-release` + a resolved ref/version. Copy the block from a sibling.
      - name: Check release and resolve ref
        id: release-check
        uses: ./.github/actions/check-github-release   # >>> FILL: correct action for the upstream <<<
        with:
          repository: CHANGEME
          age-threshold-seconds: ${{ env.RELEASE_AGE_THRESHOLD }}
          github-token: ${{ secrets.GITHUB_TOKEN }}
          trigger: ${{ github.event_name }}
          manual-ref: ${{ inputs.@@NAME_UPPER@@_REF }}

      - name: Determine if build should run
        id: decision
        run: |
          if [[ "${{ steps.secrets.outputs.available }}" == "true" && "${{ steps.release-check.outputs.new-release }}" == "true" ]]; then
            echo "should-run=true" >> $GITHUB_OUTPUT
          else
            echo "should-run=false" >> $GITHUB_OUTPUT
          fi

  build:
    needs: preflight
    if: needs.preflight.outputs.should-run == 'true'
    runs-on: ${{ matrix.arch.runs_on }}
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix:
        base_image:
          # Pin the base image(s) — copy the CURRENT matrix from the closest sibling.
          # pytorch-nested: vastai/pytorch:<dated>; derivative: vastai/base-image:<dated>;
          # external: the upstream image (e.g. org/app).
          - CHANGEME
        arch:
          - { platform: linux/amd64, suffix: amd64, runs_on: ubuntu-latest }
          - { platform: linux/arm64, suffix: arm64, runs_on: ubuntu-24.04-arm }
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Maximize build space
        uses: ./.github/actions/maximize-build-space

      - name: Derive staging tag
        run: |
          DOCKERHUB_REPO="${{ inputs.DOCKERHUB_REPO || env.DEFAULT_DOCKERHUB_REPO }}"
          IMAGE_TAG="CHANGEME"   # >>> FILL: derive from the resolved ref + base tag, per a sibling <<<
          STAGING_TAG_BASE="${{ secrets.DOCKERHUB_NAMESPACE_STAGING }}/${DOCKERHUB_REPO}:${IMAGE_TAG}"
          echo "STAGING_TAG_BASE=$STAGING_TAG_BASE" >> $GITHUB_ENV

      - name: Build and push arch image to staging
        uses: ./.github/actions/build-arch-image
        with:
          context: @@CONTEXT@@
          arch: ${{ matrix.arch.platform }}
          tag-base: ${{ env.STAGING_TAG_BASE }}
          # Pass the base image + resolved ref as the build-args your Dockerfile expects
          # (pytorch-nested: PYTORCH_BASE; derivative: BASE_IMAGE; external: <UP>_BASE +
          # a `build-contexts: |` line with `base_image_source=.`). See a sibling.
          build-args: |
            CHANGEME=${{ matrix.base_image }}
          dockerhub-username: ${{ secrets.DOCKERHUB_USERNAME }}
          dockerhub-token: ${{ secrets.DOCKERHUB_TOKEN }}

  # Live-GPU QA gate (ADR 0005): rent a real GPU, boot the freshly-built STAGING image,
  # run a functional test, and gate promotion on the verdict. Fires only when a build ran
  # (should-run). If this image genuinely cannot be functionally tested, remove this job
  # AND drop `qa` from merge-manifests/notify `needs` below — but surface that to a human.
  qa:
    needs: [preflight, build]
    if: needs.preflight.outputs.should-run == 'true'
    strategy:
      fail-fast: false
      matrix:
        include:
          # >>> FILL: one { cuda, py } per base-image matrix cell above (constants, used to
          # build the staging tag inline below — no per-cell shell derivation). <<<
          - { cuda: 'CHANGEME', py: 'CHANGEME' }
    uses: ./.github/workflows/qa-gate.yml
    with:
      repo: ${{ inputs.DOCKERHUB_REPO || '@@NAME@@' }}
      # >>> FILL: the amd64 staging tag for this cell — must match the build job's derived
      # IMAGE_TAG with the -amd64 suffix (…-cuda-${{ matrix.cuda }}-${{ matrix.py }}-amd64). <<<
      tag: CHANGEME
      template_dir: @@CONTEXT@@/templates/@@NAME@@-qa
      label: base-image-qa-@@NAME@@
      # >>> FILL: in-instance log files to stream into the run (space-separated), e.g.
      # "/var/log/portal/@@NAME@@.log" — or delete this line. <<<
      log_paths: "CHANGEME"
    secrets:
      VAST_API_KEY: ${{ secrets.VAST_API_KEY }}
      DOCKERHUB_NAMESPACE_STAGING: ${{ secrets.DOCKERHUB_NAMESPACE_STAGING }}
      SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}

  merge-manifests:
    needs: [preflight, build, qa]
    if: needs.preflight.outputs.should-run == 'true'
    runs-on: ubuntu-latest
    # Production approval gate: a manual dispatch must clear the `production` environment
    # approval before the multi-arch index is published to the prod namespace.
    environment: ${{ github.event_name == 'workflow_dispatch' && 'production' || '' }}
    strategy:
      fail-fast: false
      matrix:
        base_image:
          # Same base-image matrix as the build job above.
          - CHANGEME
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Derive image tags
        run: |
          DOCKERHUB_REPO="${{ inputs.DOCKERHUB_REPO || env.DEFAULT_DOCKERHUB_REPO }}"
          IMAGE_TAG="CHANGEME"   # >>> FILL: identical to the build job's derive step <<<
          STAGING_TAG_BASE="${{ secrets.DOCKERHUB_NAMESPACE_STAGING }}/${DOCKERHUB_REPO}:${IMAGE_TAG}"
          PROD_TAG="${{ secrets.DOCKERHUB_NAMESPACE }}/${DOCKERHUB_REPO}:${IMAGE_TAG}"
          echo "STAGING_TAG_BASE=$STAGING_TAG_BASE" >> $GITHUB_ENV
          echo "PROD_TAG=$PROD_TAG" >> $GITHUB_ENV
          MATRIX_ID=$(echo "${{ matrix.base_image }}" | md5sum | cut -c1-8)
          echo "MATRIX_ID=$MATRIX_ID" >> $GITHUB_ENV

      - name: Assemble multi-arch index in production
        uses: ./.github/actions/merge-arch-manifests
        with:
          target-tag: ${{ env.PROD_TAG }}
          source-tag-base: ${{ env.STAGING_TAG_BASE }}
          dockerhub-username: ${{ secrets.DOCKERHUB_USERNAME }}
          dockerhub-token: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Record merged manifest tag
        run: |
          mkdir -p built-tags
          echo "${{ env.PROD_TAG }}" > built-tags/tag-${{ env.MATRIX_ID }}.txt

      - name: Upload manifest tag
        uses: actions/upload-artifact@v4
        with:
          name: built-tag-${{ env.MATRIX_ID }}
          path: built-tags/
          retention-days: 1

  collect-tags:
    needs: [preflight, build, merge-manifests]
    if: always() && needs.preflight.outputs.should-run == 'true'
    runs-on: ubuntu-latest
    outputs:
      all-tags: ${{ steps.collect.outputs.tags }}
    steps:
      - name: Download all tag artifacts
        uses: actions/download-artifact@v4
        with:
          pattern: built-tag-*
          path: all-tags/
          merge-multiple: true

      - name: Aggregate tags
        id: collect
        run: |
          if [ -d all-tags ] && [ "$(ls -A all-tags 2>/dev/null)" ]; then
            TAGS=$(cat all-tags/*.txt | jq -R -s -c 'split("\\n") | map(select(length > 0))')
          else
            echo "::warning::No tags found - build may have failed"
            TAGS="[]"
          fi
          echo "tags=$TAGS" >> $GITHUB_OUTPUT

  notify:
    needs: [preflight, build, qa, merge-manifests, collect-tags]
    if: always() && needs.preflight.outputs.should-run == 'true'
    uses: ./.github/workflows/notify-slack.yml
    with:
      build-result: ${{ needs.merge-manifests.result }}
      # Distinct headline per outcome; `needs.qa.outputs.gated` is matrix-safe (all cells
      # share the repo secret, so they agree). A plain pass renders the default success.
      headline: ${{ (needs.build.result != 'success' && '@@LABEL@@ build failed') || (needs.qa.result == 'failure' && '@@LABEL@@ QA FAILED on real GPU — not promoted') || (needs.merge-manifests.result == 'failure' && '@@LABEL@@ promotion (manifest merge) failed') || (needs.qa.outputs.gated == 'true' && '@@LABEL@@ promoted — live-GPU QA passed') || '' }}
      image-name: "@@LABEL@@"
      image-ref: ${{ needs.preflight.outputs.resolved-ref }}
      image-tags: ${{ needs.collect-tags.outputs.all-tags }}
      trigger: ${{ github.event_name }}
      run-url: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
    secrets:
      SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
'''


# The QA gate (ADR 0005) boots this template with the freshly-built STAGING image (CI
# overrides image/tag via create.py --tag) and runs the functional test on it. The
# compute_cap floor is pre-set to a valid number (sm_75) so L050 passes; the launch spec
# is left to FILL because it must faithfully mirror how this image actually runs.
_QA_TEMPLATE = '''# QA template for @@LABEL@@ — the live-GPU gate boots this with the freshly-built
# STAGING image (CI overrides image/tag) and runs the functional test on it. Keep it
# minimal, private, and faithful to how the image really launches; model the launch spec
# on a sibling templates/<name>-qa (comfyui-qa, vllm-qa).
name: "@@LABEL@@ QA smoke"
image: CHANGEME
tag: latest
private: true
readme_visible: false
onstart: entrypoint.sh
# >>> FILL: complete the launch spec — the production template's ports / env /
# PORTAL_CONFIG, plus the QA functional-test provisioning (what the gate exercises on the
# GPU: a workflow to run, a model to load + a prompt, etc.). <<<
extra_filters:
  compute_cap:
    # >>> FILL: set the real minimum compute capability for this image (sm_XX x 10). <<<
    gte: 750
'''


_DEFAULT_TEMPLATE = '''# Default launch template for @@LABEL@@ — the public, user-facing template (the eventual
# recommended/marketplace listing). This is how a user launches the image to try it on a
# real GPU. Keep it faithful to how the image actually serves; the QA gate models its
# smoke on this.
name: "@@LABEL@@"
image: vastai/@@NAME@@
tag: latest
private: false
readme_visible: true
onstart: entrypoint.sh
# >>> FILL: complete the launch spec — ports / env / PORTAL_CONFIG, wiring the app's real
# interface:port into the portal (model this on how the supervisor launches the app). <<<
extra_filters:
  compute_cap:
    # >>> FILL: set the real minimum compute capability for this image (sm_XX x 10). <<<
    gte: 750
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
    # class-sanity (ADR cond. #3): the --upstream signal must agree with the class.
    # (Lint-time path<->FROM class consistency is enforced by L004.)
    if cls == "external" and not upstream:
        raise ValueError("external images require --upstream <image:tag>")
    if cls != "external" and upstream:
        raise ValueError(f"--upstream is only valid for external images, not {cls} "
                         "(did you mean --class external?)")

    sub = dict(NAME=name, NAME_UPPER=name.upper().replace("-", "_"), LABEL=label,
               PORT=str(port), UPSTREAM=upstream or "",
               CONTEXT=f"{_CLASS_DIR[cls]}/{name}",   # build-arch-image context = the image dir
               LABELS=_render(_LABELS, LABEL=label), SNAP=_SNAP)
    df_tmpl = {"derivative": _DF_DERIVATIVE, "pytorch-nested": _DF_PYTORCH,
               "external": _DF_EXTERNAL}[cls]

    d = repo / _CLASS_DIR[cls] / name
    root = d / "ROOT"
    (root / "opt/supervisor-scripts").mkdir(parents=True, exist_ok=True)
    (root / "etc/supervisor/conf.d").mkdir(parents=True, exist_ok=True)
    (root / "etc/vast_capabilities.d").mkdir(parents=True, exist_ok=True)
    (root / "etc/vast_agents").mkdir(parents=True, exist_ok=True)
    (d / "templates" / f"{name}-qa").mkdir(parents=True, exist_ok=True)
    (d / "templates" / "default").mkdir(parents=True, exist_ok=True)

    files = {
        d / "Dockerfile": _render(df_tmpl, **sub),
        d / "README.md": _render(_README_DEV, **sub),
        d / "README.template.md": _render(_README_TEMPLATE, **sub),
        d / "templates" / f"{name}-qa" / "template.yml": _render(_QA_TEMPLATE, **sub),
        d / "templates" / "default" / "template.yml": _render(_DEFAULT_TEMPLATE, **sub),
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
        # Supervisor launch scripts are exec'd directly by the .conf (command=...sh), so
        # they must be executable — write_text leaves 0644, which is fatal on launch (L051).
        if path.parent.name == "supervisor-scripts":
            path.chmod(0o755)
    return d
