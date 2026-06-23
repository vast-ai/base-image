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

## Bug → Invariant protocol (when I report a mistake/miss/regression)

A reported defect means a MISSING INVARIANT, not just a line to patch. When I say
"we missed X" or report a bug in the image tooling, do this yourself, unprompted —
do not just patch the symptom:

1. **Ground truth first** — inspect the real repo; restate the exact invariant and
   its boundary/exemptions.
2. **Linter check BEFORE the fix** — add a new `RULES` code in
   `tools/imagegen/imagegen/linter.py`; regenerate `docs/lint-rules.md`
   (`imagegen rules > docs/lint-rules.md`). Run `imagegen lint --all`: the baseline
   must stay CLEAN. If a real image fails, STOP and tell me (latent bug, or the
   invariant is wrong).
3. **Prove it bites** — a mutation test that corrupts a real image and asserts the
   new code fires. No mutation test = the check doesn't count.
4. **Then fix the source** (generator/etc.) + a round-trip/regression assertion.
5. **Show evidence, don't claim** — report the new code, the baseline result, and
   the mutation-test name. "Done" without evidence is a failure.

Don't edit beyond what this requires; surface anything larger.

## Repo-specific cautions
- **Bash for build/registry plumbing; Python 3.12 for structured/tested logic**
  (precedent: `lib/provisioner`, `portal-aio`, `tools/model-ui`). Don't introduce a
  new toolchain without a reason.
- Static checks are a fast gate, not a correctness gate — the real `docker build`
  (+ smoke test) is the correctness check (see ADR 0001).
- Several headline "invariants" in the docs are NOT real (notably
  `external port == internal + 10000`). Confirm against `docs/invariants.md` before
  encoding any rule.
