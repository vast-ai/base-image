# Context map

Where things live and what they're responsible for. A monorepo that builds the
Vast.ai Docker image family: a CUDA/ML base image + a tree of derived application
images, sharing a common runtime overlay (`ROOT/`), built and promoted to DockerHub
via GitHub Actions. **Bash for build/registry plumbing; Python for structured,
tested logic** (this split matters for tooling decisions — see §9).

## 1. Root build/release tooling (bash)
Local counterparts to CI. Model: **staging namespace → prod namespace via retag**
(no rebuild on promote).
- `build.sh` — the builder; `build_image()` wraps `docker buildx build` (base-image
  cuda/rocm/stock × ubuntu × python matrix). Supports `DRY_RUN`.
- `retag.sh` — primitive used by all promote/backup scripts; `crane copy`
  (registry-to-registry, near-instant).
- `build-staging-to-prod.sh` — promote staging → prod via `retag.sh`; strips date
  suffix, adds floating alias tags.
- `build-prod-backup.sh` / `build-prod-restore-backup.sh` — snapshot prod before a
  promotion / roll back.

## 2. The base image
- `Dockerfile` — real base build; `caddy_builder` stage then `FROM ${BASE_IMAGE}`;
  `COPY ./ROOT/ /`; full toolchain. Source of all `vastai/base-image:*` tags.
- `Dockerfile.runtime` — CUDA runtime variant from a stock (non-NVIDIA) base.
- `Dockerfile.extend` — hotfix path: re-overlay `ROOT/` + `portal-aio` on a dated
  prod image, re-run `env-hash`, no full rebuild.
- **`ROOT/` overlay** (copied to `/`, inherited by all images):
  - `etc/vast_boot.d/` — ordered boot sequence (`NN-name.sh`), run by
    `boot_default.sh`: configure-cuda → prep-env → … → supervisor-launch →
    instance-test → manifests → supervisor-wait. `first_boot/` runs once.
  - `opt/instance-tools/bin/` — PATH executables: `entrypoint.sh`, `boot_default.sh`,
    `provisioner`, `vast-capabilities`, `env-hash`, `jupyter`, etc.
  - `opt/instance-tools/lib/provisioner/` — **Python** package (`python -m provisioner`):
    phased, idempotent manifest runner (apt/git/pip/conda installers, HF/wget
    downloaders, schema/state/concurrency). Has its own `tests/`.
  - `opt/instance-tools/tests/` — bash test harness (`runner.sh`) for capabilities.
  - `opt/supervisor-scripts/` — service launch scripts (`caddy.sh`, `jupyter.sh`,
    `instance_portal.sh`, …) + `utils/` (6 sourced helpers).
  - `etc/supervisor/` — `supervisord.conf` + `conf.d/*.conf` (one per service).
  - `etc/vast_capabilities.d/NN-*.yaml` — capability fragments (base 10-,
    derivative 30-, nested 50-), merged at request time.
  - `etc/vast_agents/*.md` — AI-agent operating guides baked into images.
- Provides to all images: boot orchestration, supervisor framework, Caddy proxy +
  TLS + token auth, instance portal, provisioner, capabilities + agent-docs.

## 3. Image tree — directory layout mirrors the FROM chain
- `derivatives/<x>/` — `FROM vastai/base-image` (pytorch, tensorflow, llama-cpp,
  linux-desktop, UnrealPixelStreaming).
- `derivatives/pytorch/` — the hub; own `build-many.sh`, `torch-companions.json`,
  `install-torch-venv.sh`, `Dockerfile.{extend,multi-torch}`.
- `derivatives/pytorch/derivatives/<app>/` — `FROM vastai/pytorch` (~16: comfyui,
  a1111, sd-forge, invokeai, fooocus, swarmui, kohya_ss, fluxgym, ostris-ai-toolkit,
  unsloth-studio, oobabooga, whisper, voicebox, ace-step, wan2gp, aio-studio).
- `external/<x>/` — multi-stage wrap of an upstream image + `convert-non-vast-image.sh`
  graft (vllm, sglang, ollama, openwebui, vllm-omni).

## 4. portal-aio/ (Python)
Instance-portal web/proxy suite, launched by supervisor, shipped in every image
via `Dockerfile.extend`. `portal/portal.py` (serves capabilities at :11111),
`caddy_manager/` (generates Caddy config from `PORTAL_CONFIG`), `tunnel_manager/`
(Cloudflare), `capabilities/`, `mcp_server/` (MCP tools for agents). Own pytest
suite (`portal-aio/tests/`).

## 5. provisioning/ & provisioning_scripts/
Legacy/prototype provisioning path (distinct from the `lib/provisioner` package).
`provisioning/` = example manifests; `provisioning_scripts/*.sh` = standalone
installers — the "no dedicated image" route for an app.

## 6. tools/
- `convert-non-vast-image.sh` — installs the base toolset onto a non-Vast upstream
  (the mechanism behind `external/*`).
- `model-ui/` (**Python**, Starlette proxy) — shared inference UI for vLLM/SGLang.

## 7. CI — .github/
Per `.github/AGENTS.md`: native-arch builds (no QEMU) → arch-suffixed tags to
**staging** → `crane` merge to the **prod** multi-arch tag.
- `workflows/build-*.yml` — one per image (~21). Schedule + `workflow_dispatch`.
- `workflows/extend-*.yml` — overlay-only rebuilds. `promote-*.yml` — retag
  staging→prod. `notify-slack.yml` — reusable.
- `actions/` — composite: `build-arch-image`, `merge-arch-manifests`,
  `maximize-build-space`, and `check-{dockerhub,ghcr,github,pypi}-release` pollers.
- Secrets: `DOCKERHUB_USERNAME/TOKEN`, `DOCKERHUB_NAMESPACE`,
  `DOCKERHUB_NAMESPACE_STAGING`, optional `SLACK_WEBHOOK_URL`.
- ⚠️ Real job shape is 5-job (with `merge-manifests`), not the docs' 4-job — see
  [invariants §2](invariants.md).

## 8. Docs & references
- `CONTRIBUTING.md` — how to add an image (the invariant set; **partly stale** —
  see [invariants.md](invariants.md)).
- `.github/AGENTS.md` — canonical CI/CD conventions for agents.
- Per-image marketplace listing: `derivatives/**/templates/default/README.md` (co-located with
  the recommended `template.yml`; injected at publish with `<<LAUNCH_LINK>>` substituted — ADR
  0011). Superseded the old root `README.template.md`.
- `docs/adr/` — decision records; `docs/invariants.md`, `docs/context-map.md` (here).

## 9. Languages / toolchains (decides where new tooling goes)
- **Python 3.12 already established** in three islands: `lib/provisioner` (packaged,
  pytest), `portal-aio/` (web + MCP, pytest), `tools/model-ui/` (Starlette). →
  natural home for anything structured/testable (relevant to the ADR 0001 linter).
- **Bash dominates** build/release (`build*.sh`, `retag.sh`), in-image boot/service
  glue, and `convert-non-vast-image.sh`.
- The two are deliberately separated: Python for logic-with-tests, bash for
  Docker/registry plumbing.
- Note: root `test/` is a stray virtualenv, **not** a test suite.
