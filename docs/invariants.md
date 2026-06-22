# Invariants

Rules the image family actually relies on, **verified against the real files** (not
transcribed from docs). This is the spec a static linter should encode (see
[ADR 0001](adr/0001-image-scaffolding-tooling.md)). Where reality diverges from
`CONTRIBUTING.md` / `.github/AGENTS.md`, it is flagged — **reality wins here.**

Classes: **derivative** (`FROM vastai/base-image`), **pytorch-nested**
(`FROM vastai/pytorch`, under `derivatives/pytorch/derivatives/`), **external**
(`external/*`, multi-stage wrapping an upstream image).

> ⚠️ **Key finding for the linter:** the pattern is *less* uniform than the docs
> claim. Some headline "rules" cannot reach a clean baseline and **must not be
> gated** (see §3). Most importantly, **`external port == internal + 10000` is
> NOT a real invariant** — this directly affects ADR 0001's plan.

---

## 1. Hard invariants — safe to GATE (clean baseline reachable)

All verified clean across existing images.

- **3 LABELs.** Exactly three top-level `LABEL` lines, keys:
  `org.opencontainers.image.source`, `org.opencontainers.image.description`
  (value ends `suitable for Vast.ai.`), `maintainer="Vast.ai Inc <contact@vast.ai>"`.
- **`env-hash` trailer.** Final build instruction is `env-hash > /.env_hash`.
  *(External: it's the last `RUN`, before only `ENTRYPOINT`/`CMD` — not literally
  the last line.)*
- **`COPY ./ROOT /`** present exactly once. *(External also has
  `COPY --from=base_image_source /ROOT /`.)*
- **External graft block** (`external` only): the fixed env block → 4 `COPY`s
  (incl. `convert-non-vast-image.sh`) → the convert `RUN` → `COPY ./ROOT /` →
  `ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]` + `CMD []`.
- **No surviving `--torch-backend auto`.** The only `auto` occurrences are `sed`
  rewrites that *replace* it with a concrete backend. Flag `auto` only when it's
  the *argument of an install command*.
- **`uv pip` only**, never bare `pip install` (pytorch-nested; spot-check externals).
- **Supervisor util source ORDER.** When utils are sourced, they appear as a
  *subsequence* of: `logging.sh → cleanup_generic.sh → environment.sh →
  exit_serverless.sh → exit_portal.sh`. Zero inversions exist. Match on path
  (`logging.sh` is sometimes called with an argument).
- **conf.d ↔ script ↔ program-name triple** (STRONGEST invariant, 60/60 clean):
  every `etc/supervisor/conf.d/*.conf` has `environment=PROC_NAME="%(program_name)s"`,
  `command=/opt/supervisor-scripts/<x>.sh`, and `[program:NAME]` where `NAME` ==
  conf filename stem. Bonus checkable: the `command=` target script exists on disk.
- **External `05-<name>-env.sh`** present setting `PORTAL_CONFIG` (externals + a
  few derivatives).
- **PORTAL_CONFIG anchors.** First entry always
  `localhost:1111:11111:/:Instance Portal`; a Jupyter entry present.
- **One `build-<name>.yml` per image.**
- **External build passes `--build-context base_image_source=.`** (all 5). This is
  what makes the otherwise-undeclared `base_image_source` stage resolve.

> Note: "one `build-<name>.yml` per image" is **not** universal — 6 images
> (fooocus, kohya_ss, oobabooga, swarmui, tensorflow, UnrealPixelStreaming) build
> via shared/other workflows. The linter treats workflow presence as a WARN (L030),
> not a gate.

## 2. Conditional invariants — GATE per-class, with exceptions encoded

- **FROM matches class** — EXCEPT: `aio-studio` builds on a custom
  `robatvastai/aio-studio:base-*` (not `vastai/pytorch`); `external/openwebui`'s
  upstream stage has **no `AS` alias** (others use `*_build`). The
  `vast_base_image`-first / upstream-second ORDER *is* hard across all externals.
- **torch-drift guard** (pytorch-nested) — present 16/17. EXEMPT: `aio-studio`
  (per-app venvs). ⚠️ The doc describes the **stale torch-only** form; reality
  checks the 4-package ecosystem (`torch|torchvision|torchaudio|torchcodec`).
- **strip upstream torch pins** (`sed` before install) — strong convention, not
  universal; gate only "if requirements installed, a torch-strip precedes it."
- **CI job set** — app images: `{preflight, build, merge-manifests, collect-tags,
  notify}` (allow `resolve-refs` variant; allow drop of `merge-manifests` for
  single-arch, e.g. voicebox). EXEMPT: `base-image`/`pytorch` (bespoke pipelines),
  `aio-studio-base` (2-job). ⚠️ Docs say 4-job and **omit `merge-manifests`** —
  wrong; 5-job dominates (16 workflows).
- **`MATRIX_ID` / built-tag** — `md5sum | cut -c1-8`; artifact `built-tag-*`.
  Strong convention in app workflows.

## 3. NOT invariants — DO NOT GATE (doc claims false against reality)

- ❌ **`external port == internal + 10000`.** Widely violated: Jupyter terminal
  (delta 0), `ollama` (reversed: `21434:11434`) vs `openwebui` (`11434:21434`) —
  the two even disagree on the *same* service; `aio-studio` pervasively (columns
  appear transposed). **This kills a binding assumption in ADR 0001.** At most a
  *soft warning* excluding known-exempt labels; possibly flag "likely transposed
  columns."
- ❌ **Fixed util SET** ("must source all 4"). The set varies legitimately;
  only the order is invariant. *(And there are 6 utils, not 4 —
  `exit_serverless.sh`, `pty.sh` are undocumented.)*
- ❌ **Uniform cron** `'0 0,12 * * *'`. Deliberately staggered across images.
- ❌ **Required `DEFAULT_MULTI_ARCH` / `RELEASE_AGE_THRESHOLD`.** First exists in
  one workflow; the latter is class-dependent (`COMMIT_AGE_THRESHOLD` for git-ref
  images) and absent in several. Only `DEFAULT_DOCKERHUB_REPO` is near-hard.
- ❌ **`set -euo pipefail` in every RUN.** Only the primary install RUN reliably
  has it.
- ❌ **The docs' specific `vast_boot.d` script list.** Numbering scheme is a rule
  (`^[0-9]{2}-.*\.sh$`); the exact list is mid-migration and already wrong in docs.

## 4. Real but NOT statically checkable (linter blind spots → need the build)

These are why ADR 0001 condition #1 holds: **static lint is the fast gate, the
real `docker build` + smoke test is the correctness gate.**

- torch ecosystem *actually* unchanged after install (guard presence ≠ success).
- CPU smoke-tests pass (presence checkable; success runtime-only).
- PORTAL_CONFIG ports match the port the app actually binds.
- Tag commit-hash-vs-version date suffix (depends on the runtime-resolved ref).
- `base_image_source` build-context *content*.
- single shared `/venv/main` assumption (false for aio-studio by design).

**Feasible future cross-file static check:** every `exit_portal.sh "<Label>"` in
an image should have a matching `:<Label>` entry in that image's `PORTAL_CONFIG`
(catches typos). Not currently enforced.

## 5. Docs needing correction before they can be trusted

`CONTRIBUTING.md` / `.github/AGENTS.md` are stale on: the torch guard form (§2),
the boot-sequence list (§3), the 4-job pipeline + missing `merge-manifests` (§2),
the undeclared `base_image_source` stage (§1), the missing utils
`exit_serverless.sh`/`pty.sh` (§1), the port +10000 claim (§3), and uniform cron (§3).
