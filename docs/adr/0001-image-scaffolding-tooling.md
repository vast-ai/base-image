# ADR 0001 — Tooling + skill for scaffolding new images

- **Status:** Accepted (conditional — see Binding conditions), **revised 2026-06-22 — see Revision**
- **Date:** 2026-06-22
- **Process:** idea brief → red-team gate → 2 blind architects → 3-lens blind panel → synthesis (panel red-team served as final gate)

## Revision (2026-06-22) — invariants verified against the repo

Before building, the documented invariants were verified against the actual files
(see [docs/invariants.md](../invariants.md)). The decision's *shape* holds, but
findings change scope:

- **DROPPED: port-arithmetic gate and generation.** `external port == internal +
  10000` is **not a real invariant** — widely violated (Jupyter delta 0; `ollama`
  reversed vs `openwebui`; `aio-studio` columns transposed). The generator must
  **not** fabricate a `+10000`; the linter must **not** gate it (soft warning at
  most). This falsifies the original Decision/Context assumption that listed port
  arithmetic as a generated + checked rule.
- **NARROWED: linter scope = `docs/invariants.md` §1–2** (the verified gateable
  set). Notable corrections it must respect: utils are an *ordered subsequence*
  (not a fixed set of 4); CI is the **5-job** shape (incl. `merge-manifests`), not
  4; `base_image_source` is injected via `--build-context` (don't flag it as an
  undeclared stage); class/torch exceptions exist (`aio-studio`, `openwebui`,
  `voicebox`).
- **Reversal condition NOT triggered:** a clean baseline *is* reachable on the
  verified subset (the `conf.d↔script↔program-name` triple is 60/60 clean). The
  pattern is established enough — just narrower than the docs claim.
- **Docs are stale** (`CONTRIBUTING.md`, `.github/AGENTS.md`) — see invariants §5;
  correcting them is in scope.

## Context

Adding a new image to this monorepo (`derivatives/<x>`, nested
`derivatives/pytorch/derivatives/<x>`, or `external/<x>`) is currently manual,
driven by `CONTRIBUTING.md` + `.github/AGENTS.md`. The pattern is dense and
invariant-heavy (exactly 3 LABELs; `PORTAL_CONFIG` external port = internal +
10000; commit-hash tags append an ISO date while version tags must not; torch-
drift guard; supervisor scripts source 4 utils in fixed order; `env-hash`
trailer; strict 4/5-job CI shape with a `base_image × arch` matrix to staging +
`crane` manifest merge). We want to automate the mechanical bulk without
producing plausible-but-wrong infrastructure.

A red-team review of the original "an agent generates everything" idea rated it
**fatal as framed**: a new image is an all-green PR diff a reviewer can't
diff-check, real validation needs GPUs/secrets/build, and the "which class /
does it warrant an image" call is exactly what LLMs are worst at. The idea was
reshaped before any design.

## Decision

Adopt a four-part shape:
1. **Human picks the image class** (not the agent).
2. A **deterministic generator** emits the mechanical ~80% (Dockerfile skeleton
   with correct ARG/FROM/3 LABELs, supervisor `.conf`, `build-<name>.yml`,
   README from template, port arithmetic).
3. The **LLM/skill drafts only the judgment residue** (install steps, external
   graft, app launch body) inside clearly fenced blocks.
4. A **static invariant-linter** (no GPU/registry/build) gates before PR.

### Implementation: linter-as-B, maintained-as-A

- **Linter = robust, parsed-model, tested** (the "Design B" approach): parse each
  artifact once into a typed model (Dockerfile tokenizer → instruction list;
  `configparser` for `.conf`; `yaml.safe_load` for YAML/CI), checks operate on
  structure not regex, stable machine codes per finding. **Python**, reusing the
  repo's existing pytest setup (portal-aio, model-ui). Tests: one **mutant per
  invariant** (proves each check has teeth), a **regression net** over all
  existing images, and a **generator round-trip** test.
- **Rejected: the pure shell linter** (the "Design A" approach, grep/awk +
  *optional* `yq`). It scored highest on team-fit but the correctness lens rated
  it 4/10 and the red-team called it *trending fatal as specified*: grep/awk over
  YAML cannot model the CI job-shape DAG, and optional-`yq`-with-graceful-
  degradation yields **silent false passes** and machine-dependent verdicts on
  the single hardest invariant. A gate you can't trust is worse than no gate.
- **Generator** shares the one Python/Jinja2 toolchain (plain, reviewable
  templates); do **not** split languages within the tool.

## Binding conditions (non-negotiable — from the panel red-team)

If any is refused, the approach reverts to fatal (it would manufacture false
confidence — a green check that ships broken infra past a disengaged reviewer):

1. **Static lint is the FAST first gate, not the correctness gate.** The existing
   `docker build` (+ a smoke test) is the real check behind it. "Green lint" =
   *ready to build*, never *correct*. Conformance ≠ correctness.
2. **Defeat drift structurally:** the linter is authoritative; a CI test asserts
   `CONTRIBUTING.md`/`.github/AGENTS.md` agree with the linter (or the
   human-facing checklist is generated FROM the linter rules). Two hand-
   maintained encodings of one ruleset will otherwise diverge by default.
3. **Class-sanity check:** a heuristic that challenges the human's class choice
   (*"this looks like class X but you declared Y"* from FROM/upstream cues) — the
   one verification on the unverifiable input. It advises; it does not override.
4. **Minimal deps + a named owner** for the Python toolchain (the maintainability
   risk in a bash-first team is dep rot and an under-serviced test suite). Triage
   regression-net failures on existing images as *real bug vs wrong invariant*
   with a dated, reasoned allowlist — never silently pin a bug.
5. **Fence escape hatch:** if a correct fix needs changes outside the residue
   fence (base bump, extra stage), the skill surfaces it to the human rather than
   contorting the fix to fit the box.

## Consequences

- **Positive:** correctness oracle you can trust; mechanical 80% correct by
  construction; LLM confined to where judgment actually lives; the linter is
  independently valuable (a regression net for all existing images) and exists
  regardless of the generator/skill.
- **Negative / accepted cost:** introduces Python as the canonical image-tooling
  path in a bash-heavy repo (mitigated: Python already present, minimal deps,
  named owner); the generator/skill are conveniences that hang off the linter and
  can lag without breaking the gate.

## Build order (linter first — contract before generator)

1. Write the linter; run `lint --all` over the real repo; triage to a clean
   baseline (proves the invariants are real).
2. Add mutant fixtures + regression net + CI job (lint + pytest, advisory
   hadolint/actionlint).
3. Build the generator (Jinja2 templates per class, fenced residue); add the
   round-trip test.
4. Wire `lint --all` into the CI **preflight** job; add the docs⟷linter agreement
   test (condition 2) and the class-sanity check (condition 3).
5. Author the Claude skill: confirm human class → class-sanity challenge →
   generate → fill only fenced residue (escape hatch per condition 5) → lint
   until clean → PR → existing build CI is the real gate.

## Implementation status (2026-06-22)

Built on `feature/CON-1585-image-linter`, each layer hardened by adversarial review:
- **Linter** (`tools/imagegen`) — checks per §1–2; mutation-tested; clean baseline over 27 images.
- **Generator** (`imagegen new`) — templates from real images; L040 gates unfilled skeletons.
- **Skill** (`.claude/skills/new-image`) — orchestrates the flow with the contract above.
- **Binding condition #1** — honoured: static lint is the fast gate; the README/skill
  state the real `docker build` is the correctness gate.
- **Binding condition #2** (docs⟷linter agreement) — DONE: `RULES` is the single source
  of truth, `docs/lint-rules.md` is generated (`imagegen rules`), and tests fail on drift
  between the catalog, the emitted codes, and the doc.
- **Binding condition #3** (class-sanity) — DONE mechanically: `--upstream` must agree
  with the class, and L004 enforces path⟷FROM class consistency at lint time; the skill
  adds the human-facing "challenge the class" step.
- **Port arithmetic** — dropped (see Revision); ports are FILL markers, not fabricated.

## What would reverse this

- If the static linter cannot reach a clean baseline over existing images
  because the "invariants" aren't actually consistent → the pattern isn't as
  established as believed; stop and codify it first.
- If new-image volume is low enough that the manual + good-docs process already
  wins → don't build the generator/skill; ship only the linter as a regression
  net (still net-positive).
- If no named owner can be committed to the Python toolchain → reconsider a
  smaller shell linter with `yq` REQUIRED (not optional) and a known-bad fixture
  corpus.
