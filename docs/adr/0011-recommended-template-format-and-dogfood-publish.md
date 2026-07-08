# ADR 0011 — The generated launch template is a production-ready recommended template, dogfood-published live

- **Status:** Accepted
- **Date:** 2026-07-06
- **Decision owner:** Rob Ballantyne
- **Process:** idea (running `/new-image` should end at a production-ready template) →
  ground-truth against vast_landing's recommended templates → challenge → adversarial review
  (which rated the first-draft "auto-publish a public production template every run" **fatal
  as specified**) → decisions → this record. Extends [ADR 0010](0010-unify-launch-and-qa-template.md).

## Context

ADR 0010 made `templates/default/template.yml` the single launch template the live-GPU QA
gate boots. But the generator scaffolds a **thin** template + a thin root
`README.template.md` whose launch link is **hardcoded** to `cloud.vast.ai/?ref_id=525202…`
(the QA account) — so a freshly-generated image is not a production-grade recommended
template, and its marketplace doc is authored in a form the publish tooling can't even
consume.

The canonical production "recommended templates" live in the separate **vast_landing** repo
(`scripts/template_manager/recommended_templates/templates/NNN-name/{template.yml, README.md}`).
Theirs carry the full production spec (`href`, `repo`, `tag: "@vastai-automatic-tag"`, `desc`,
`recommended_disk_space`) and a **structured README** using the `<<LAUNCH_LINK>>` **placeholder**
(substituted at publish by `create.py`) plus a Licenses section. base-image's `create.py` (ported
from vast_landing) **already** substitutes `<<LAUNCH_LINK>>`, models those fields, and
**auto-discovers a co-located `README.md`** in a template dir — so the tooling is ready; only
the generator and skill lag.

Goal: `/new-image` should end with (a) a **production-ready** recommended-format template a
human can promote into vast_landing unchanged, and (b) a **live template the author can
launch immediately** to dogfood the freshly-built image.

## Options considered

- **Auto-publish a public, production-looking template every run (first draft — REJECTED).**
  An adversarial review rated this fatal: pre-promotion the prod image `vastai/<name>` does
  not exist, so it can only point at the **staging** image; `create.py` is **POST-only** by
  design (`template_manager.py` — no update/idempotency, an upstream-API constraint), so each run
  **litters a new public template**; and the referral URL is built from the **QA account**
  key. Net: it ships a staging image under a production-looking public listing from the wrong
  account and accretes orphans nothing cleans up.
- **Make base-image `templates/default/` the automated single source, syncing to vast_landing
  (REJECTED for now).** Would make "single source" literally true, but requires building and
  maintaining a base-image→vast_landing sync/generation step across a private repo boundary —
  scope the review did not justify. vast_landing remains the production publish home.
- **Advisory WARN linter rule (REJECTED).** A WARN over a fleet where 20/21 legacy
  `README.template.md` hardcode the ref link is decorative — it neither fails CI nor keeps a
  clean baseline. Rejected in favour of a **scoped ERROR** (below).
- **Format upgrade + private idempotent dogfood publish + scoped-ERROR linter (CHOSEN).**

## Decision

1. **Recommended-template format is the scaffold.** The generator emits
   `templates/default/template.yml` with the production field set (`name`, `image:
   vastai/<name>`, a **concrete** `tag`, `href`, `repo`, `desc`,
   `recommended_disk_space`, `extra_filters.compute_cap` floor, `private: false`,
   `readme_visible`), and a **rich** `templates/default/README.md` modelled on vast_landing's
   recommended READMEs — structured sections + a Licenses block + the **`<<LAUNCH_LINK>>`
   placeholder** (never a hardcoded ref link). The thin root `README.template.md` is retired
   (its content was never consumable — `create.py` auto-discovers `templates/default/README.md`,
   not a root file). The root `README.md` (developer docs) is unchanged.

2. **Per-run publish is a PRIVATE, staging-pointed, IDEMPOTENT dogfood template.**
   `imagegen publish <name>` creates a `private: true` template on the QA account pointed at
   the freshly-built **staging** image, named from the template's `name`, **deleting the
   previously-published template of record first** (id tracked in the image's `.qa/publish.json`
   ledger) so runs do not accrete duplicates. A *failed* delete is not silently dropped — the
   id is kept in the ledger's `orphaned` list (WARN) so it can be cleaned up. **Idempotency
   boundary:** the ledger lives in gitignored `.qa/`, so delete-prior only fires on the machine
   that published; a fresh clone (or second machine) cannot see prior ids and would add one
   more private template — acceptable for a dogfood aid, documented here. It is a launch-it-now
   dogfood aid, explicitly **not** a public production listing. The skill runs it in Step 7
   after QA is green and hands back the launch link.

3. **Production publish stays vast_landing's job.** The public, prod-image, prod-account
   recommended template is published through the existing vast_landing process at promotion —
   **not** by the per-run skill. base-image `templates/default/` is the production-*ready*
   source a human promotes into vast_landing unchanged; there is **no automated sync** (an
   accepted, explicit gap — see Consequences).

4. **Linter L052 (ERROR), scoped to `templates/*/README.md`:** a shipped marketplace README
   that contains a Vast launch link must use `<<LAUNCH_LINK>>`, not a hardcoded
   `cloud.vast.ai…?ref_id=…` URL. Scoping to the co-located template READMEs keeps the baseline
   clean (the legacy root `README.template.md` files sit at a different path, unmatched) while
   biting exactly where publishing consumes the file. Catalogued in `RULES`; `docs/lint-rules.md`
   regenerated; a mutation test proves it fires.

## Binding conditions

- **The publish step is private + idempotent + staging-scoped.** If it ever publishes a
  public template, or POSTs without deleting the prior, or points a *production* listing at a
  staging image, this decision is void — that is the rejected footgun.
- **L052 is an ERROR that leaves the 29-image baseline CLEAN.** If it can only pass as a WARN
  or by dirtying the baseline, it does not ship (repo Bug→Invariant protocol).
- **Application templates pin a CONCRETE tag, never `@vastai-automatic-tag`.** `@vastai-automatic-tag`
  auto-selects the newest tag (possibly an unvetted build) and is reserved for the foundational
  images (`base-image`, `pytorch`, and the like). Application recommended templates — everything
  `imagegen new` scaffolds — must pin a specific known-good tag following the build scheme
  (`<ref>-cuda-<ver>-py<n>`), matching the vast_landing recommended set (e.g. vllm `v0.21.0-cuda-13.0`,
  comfyui `v0.22.0-cuda-12.9-py312`). The scaffold ships `tag: "CHANGEME"` so the author must
  consciously pin it; it is safe to leave through QA (always overridden by `create.py --tag`) but
  must be a real tag before the production listing ships. *(This supersedes the first draft of
  this ADR, which wrongly defaulted apps to `@vastai-automatic-tag`.)*

## Consequences

- **Positive:** `/new-image` ends at a production-ready recommended template plus a
  launch-now dogfood link; the marketplace README is finally in a consumable location and
  form; the exact hardcoded-ref-link defect is now caught by an ERROR at the point of use.
- **Accepted negative — no automated base-image→vast_landing sync.** `templates/default/` and
  the vast_landing recommended set can diverge; keeping them aligned at promotion is a manual
  step. We chose this over building cross-repo sync machinery now. Revisit if divergence bites.
- **Migration tail:** existing images keep their root `README.template.md`; only newly
  generated (and deliberately backfilled) images use `templates/default/README.md`. Backfill
  is per-image, tracked, not forced by this change.
- **QA path unchanged in behaviour, plus one detail:** with a co-located `README.md`, each QA
  run now auto-injects that README (and does one extra referral-URL round-trip) into the
  throwaway QA template it immediately deletes — harmless, noted so it is intentional.

## What would reverse this

- If divergence between `templates/default/` and the vast_landing production set causes a
  real user-facing mistake, build the automated sync (the rejected option) — the "single
  source" is only aspirational until then.
- If `@vastai-automatic-tag` proves unresolvable for new repos at promotion, drop it from the
  scaffold in favour of a concrete tag derivation.
