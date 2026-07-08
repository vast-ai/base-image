# ADR 0012 — ADRs stay in-repo; a content guardrail, not a relocation

- **Status:** Proposed
- **Date:** 2026-07-08
- **Decision owner:** Rob Ballantyne

## Context

`docs/adr/` holds the project's decision records, and `base-image` is a **public**
repo. Every ADR to date (0001, 0005, 0008–0011) documents internal
engineering *process/ops* — scaffolding tooling, the live-GPU QA gate, publish
tooling, the self-healing QA loop, template internals — not the image contract an
external consumer of these Docker images needs.

Three concerns motivate revisiting where they live:

- **Public exposure.** Internal reasoning is world-readable. This is not
  hypothetical: ADR 0005's condition 8 documented the QA harness's auth channel
  (which token, over which transport) and a named gap in the secret redactor — an
  attacker's map. (Removed and generalized when this ADR was drafted; the specific
  analysis moved to CON-1585.)
- **Wrong audience / clutter.** Someone cloning the image repo gets internal
  decision docs that are not about the images they consume.
- **Maintenance / staleness.** Decisions can drift from what the code actually
  does — the same rot CLAUDE.md already flags in `CONTRIBUTING`/`.github/AGENTS.md`.

These pull in **opposite directions**. Exposure and clutter argue for moving ADRs
*out* of the public image repo. Staleness argues the reverse: ADRs here are kept
honest by **co-location with their enforcement** — an ADR that changes a rule moves
in the same PR as the linter `RULES` code and `docs/invariants.md`, and tests
(`test_rules_catalog_matches_emitted_codes`, the stale-exception guard) fail when
the catalog drifts. That weld is the core of the working protocol in CLAUDE.md.
Relocating ADRs to an external wiki fixes exposure/clutter and makes staleness
**worse**. No file move satisfies all three.

## Options considered

- **A. Relocate all ADRs to Confluence/Jira.** Fixes exposure and clutter.
  **Rejected:** severs the decision from the code and linter rule it governs, so it
  rots — the exact failure mode already seen in `CONTRIBUTING`/`AGENTS`. Breaks the
  ADR ↔ `RULES` ↔ `invariants.md` single-PR discipline.
- **B. Move `tools/imagegen` + its ADRs into a private tooling repo.** Fixes
  exposure and clutter and keeps *some* co-location (with the tooling).
  **Rejected unless the tooling is meant to be private anyway:** the linter governs
  images that live *in this public repo*, so the rules and the images they lint end
  up split across repos — a heavier structural cost than the exposure it removes.
- **C. Status quo — all ADRs in-repo, no content rule.** Preserves co-location.
  **Rejected:** the 0005 condition-8 leak shows content does not self-police; "keep
  everything, change nothing" leaves exposure unaddressed.
- **D. Keep ADRs in-repo; add a content guardrail (chosen — proposed).** Do not
  move files; change what an ADR is *allowed to contain*. Cut the exposure surface
  at the source while keeping the anti-staleness weld.

## Decision

**ADRs stay in `docs/adr/` in this repo.** What an ADR may contain is bounded:

An ADR records the **decision, the rejected alternatives, the rationale, and a
pointer to the enforcing rule/invariant** — and nothing that is operationally
sensitive on a public repo. Specifically, an ADR MUST NOT contain:

- credentials, tokens, account identifiers, or keys (names or values);
- an exploitable weakness map — which auth token, which transport, which redactor
  rule fails to match which value, which endpoint is soft;
- internal account operations or business posture that an external reader has no
  need for and an adversary could use.

That material lives in the **linked Jira issue** (the `CON-xxxx` reference already
present in 0001/0005/0008). The public ADR states *that* a trade-off was made and
*why*, and points to the ticket for the sensitive specifics. General security
*design rationale* that is not an exploit map (e.g. "the QA key is capped so blast
radius = balance") is fine to record — the line is exploit-map vs. design-intent.

Every ADR carries its `CON-xxxx` link so the sensitive detail has a home.

## Binding conditions

1. **Every ADR links a CON ticket** (or states none applies). Without a home for
   the excised detail, the guardrail just deletes information.
2. **The guardrail is enforced, not just described.** Candidate enforcing artifact:
   a linter/CI check that scans `docs/adr/**` for credential-shaped tokens and
   flags them (in the spirit of the Bug→Invariant protocol). If adopted, add the
   `RULES` code and regenerate `docs/lint-rules.md`; until then the guardrail is
   review-enforced and this condition is unmet.
3. **CLAUDE.md is updated in the same change** that accepts this ADR — the
   "record decisions as ADRs in `docs/adr/`" instruction gains the content bound,
   so the convention is codified, not silently amended.

If any condition is refused, this decision is void.

## Consequences

- Exposure surface drops to the ADR's *decision-level* content; sensitive specifics
  live behind Jira auth.
- The anti-staleness weld (co-location + catalog tests) is preserved — the driver
  that a relocation would have worsened.
- A small ongoing discipline cost: authors must split "what/why" (public ADR) from
  "sensitive how" (ticket), and reviewers must catch leaks until a lint check does.
- Clutter for image consumers is reduced only marginally (the files still live
  here). Accepted: the co-location value outweighs a handful of small files.

## What would reverse this

- The tooling (`tools/imagegen` and the QA machinery) moving to a private repo for
  independent reasons — then its ADRs follow the code and option B becomes correct.
- The guardrail proving unworkable in review (leaks keep landing) with no viable
  lint enforcement — then escalate to relocating the process/ops ADRs out of the
  public repo despite the staleness cost.
