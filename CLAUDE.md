# CLAUDE.md — base-image

Monorepo for the Vast.ai Docker image family (base image + derivative/external app
images sharing the `ROOT/` overlay, built and promoted to DockerHub via GitHub
Actions). Orient with [docs/context-map.md](docs/context-map.md).

## Ground truth (read before non-trivial work)
- [docs/context-map.md](docs/context-map.md) — modules, responsibilities, where
  things live.
- [docs/invariants.md](docs/invariants.md) — the rules that must hold, **verified
  against reality**. The authoritative `CONTRIBUTING.md` / `.github/AGENTS.md` are
  partly STALE; where they disagree with `invariants.md`, reality wins.
- [docs/adr/](docs/adr/) — decisions + rejected alternatives. Read these fresh.

## How to work in this repo (expert-panel workflow)
- For any non-trivial decision, run /panel (or /forge for idea→plan) BEFORE building.
- Brief experts with the ARTIFACT only — never my preference or authorship.
- Before claiming done, /redteam the change.
- Record decisions as ADRs in docs/adr/ (template: docs/adr/0000-template.md).
  Experts read these fresh each call.
- Surface expert disagreement to me RAW; do not pre-resolve it.
- Keep docs/invariants.md and docs/context-map.md current as the project grows.

## Repo-specific cautions
- **Bash for build/registry plumbing; Python 3.12 for structured/tested logic**
  (precedent: `lib/provisioner`, `portal-aio`, `tools/model-ui`). Don't introduce a
  new toolchain without a reason.
- Static checks are a fast gate, not a correctness gate — the real `docker build`
  (+ smoke test) is the correctness check (see ADR 0001).
- Several headline "invariants" in the docs are NOT real (notably
  `external port == internal + 10000`). Confirm against `docs/invariants.md` before
  encoding any rule.
