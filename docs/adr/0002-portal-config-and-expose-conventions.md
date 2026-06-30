# ADR 0002 — Bake PORTAL_CONFIG defaults + EXPOSE Caddy-front ports across all images

- **Status:** Accepted (conditional — see Binding conditions)
- **Date:** 2026-06-23
- **Decision owner:** Rob Ballantyne
- **Process:** idea brief → critical review → competing designs → multi-dimension review
  (feasibility / maintainability / risk) → synthesis → final review gate on the plan.

## Context

Creating a Vast.ai marketplace/frontend template currently requires the template
author to hand-author the published port mappings and the `PORTAL_CONFIG` env var
per template — manual, duplicated, error-prone. The proposal: make each image
*self-describing* so template creation derives ports from the image.

Verified reality that shapes the decision:

- **`EXPOSE` is used in 0 of 33 Dockerfiles.** `PORTAL_CONFIG` is baked in only 9
  images via `ROOT/etc/vast_boot.d/05-<name>-env.sh`, always behind an
  `if [[ -z $PORTAL_CONFIG ]]` guard (bake a default; the launch template
  overrides). The other 24 rely entirely on the template.
- **Vast auto-maps `EXPOSE`d ports.** Per the Vast Docker-execution docs, any
  `EXPOSE`d port becomes an external port request. This is the friction reduction:
  EXPOSE-ing the Caddy-front port makes `VAST_TCP_PORT_<ext>` exist, which (per the
  parser, below) is what makes Caddy actually stand up the proxy site — without the
  template author adding `-p`. 64-port/instance limit; 8080/22 auto-opened.
- **Authoritative `PORTAL_CONFIG` format** (`portal-aio/caddy_manager/caddy_config_manager.py:26`,
  `hostname,ext,int,path,name = s.split(':',4)`): `localhost:<external>:<internal>:<path>:<Label>`,
  pipe-separated. **`CONTRIBUTING.md` documents this order BACKWARDS** — must be fixed.
- **Caddy proxy gate** (`caddy_config_manager.py:217`):
  `if external_port == internal_port or not VAST_TCP_PORT_<external>: continue`.
  Caddy stands up an auth site on `:external` ONLY when `external≠internal` AND the
  host mapped it. **Equal-port (`ext==int`) entries are skipped — no site, no auth**;
  they exist only as portal-tab metadata.
- **Multi-URL convention:** an app needing multiple tabs uses ONE proxied entry
  (`ext≠int`) to force Caddy, plus subsequent equal-port (`ext==int`) entries purely
  for tab/URL building (avoids a duplicate-Caddy-site clash). So one external port
  value can appear as both a proxied external and an equal-port external.
- **The loopback-behind-Caddy security invariant is hard** (see
  [docs/invariants.md](../invariants.md)); the repo has had two prior incidents of
  services binding `0.0.0.0` and being exposed. `EXPOSE` + Vast auto-map turns a
  latent `0.0.0.0` bind into a live public exposure.
- **`external == internal + 10000` is NOT a real invariant** (invariants §3); external
  ports are arbitrary per image. Known data bugs already exist: aio-studio `7861:7861`
  (Wan2GP binds `17861`), ACE-Step entry `:3000` while it launches `--port 8001`.
- Existing tooling [ADR 0001](0001-image-scaffolding-tooling.md): a Python generator +
  tested static invariant linter (`tools/imagegen`), `L0xx` codes, mutant-per-invariant
  tests, docs generated from the RULES catalog. Linter = FAST gate; `docker build` +
  smoke = correctness gate.

## Options considered

- **Option 1 — Enforce, don't deduplicate (CHOSEN).** Keep two hand-authored
  encodings (`EXPOSE` in the Dockerfile, the `PORTAL_CONFIG` default in
  `05-<name>-env.sh`) and add cross-artifact linter checks that prove they agree.
  Drift is *detected* every CI run, not made impossible. Won all three lenses
  (feasibility 9, maintainability 8, risk 8). Decisive reasons: lowest net-new
  machinery, perfect fit with the existing linter-as-gate discipline, both encodings
  stay greppable at rest, per-image migration blast radius, security check is local
  and fails safe.
- **Option 2 — Single declarative source → generate everything.** A `services:` block
  in each image's capability YAML; `imagegen sync` generates `EXPOSE` + the
  `PORTAL_CONFIG` default into marked regions; round-trip byte-compare in CI. Rejected
  as the spine: it makes drift *structurally impossible* (its real edge) but imports a
  new contributor verb, marker-integrity-in-bash, a risky 33-image backfill, and a
  runtime-behaviour change for the 24 previously-undefaulted images — and "drift
  impossible" is mis-priced when "drift detected each CI run" is the standard the repo
  already lives by. **Grafted:** its mandatory *explicit declaration* of equal-port /
  direct entries (so an unauthenticated port is never an emergent accident), and its
  matched-pair generator scaffold.
- **Option 3 — Dockerfile ENV as source, boot-time derivation.** Two coordinated ENV
  vars per Dockerfile + `EXPOSE ${VAR}` + a shared base boot script assembling
  `PORTAL_CONFIG`. Rejected (feasibility 5, maintainability 4, risk 3): premise
  (PORTAL_CONFIG as Dockerfile ENV) is contrary to the repo; relies on undocumented
  `EXPOSE ${MULTI_PORT_VAR}` expansion; a base `EXPOSE` inherited by all 33 images at
  once is exactly the broad-blast shape of the two prior incidents; and it forfeits a
  greppable `PORTAL_CONFIG` literal at rest. Kept only as a warning: do **not**
  centralise via base inheritance; migrate per-image/family.

## Decision

Adopt **Option 1**, grafting Option 2's explicit equal-port declaration + matched-pair
scaffold.

- **P0 prerequisite:** fix `CONTRIBUTING.md` field order to the parser's
  (`hostname:external:internal:path:name`); extend the docs⟷linter agreement test to
  cover port-field semantics.
- **A shared `portal.py` parser** mirrors `caddy_config_manager.py:26`, pinned by a
  test against the real parser, aggregating entries **by external port**.
- **Set model** (a port is EXPOSE-safe iff *some* entry proxies it):
  - `proxied_ext = {ext | ∃ entry ext≠int}`
  - `forbidden = ({all int values} ∪ {ext | ∃ entry ext==int}) − proxied_ext`
    (= ports no entry proxies)
  - `required = proxied_ext − base_allowlist`
  - `required` and `forbidden` are **disjoint by construction**, so a port proxied by
    one entry stays EXPOSE-safe even if it is equal-port in another entry (the
    multi-URL convention).
- **Linter checks** (codes in RULES so `docs/lint-rules.md` regenerates):
  - **L050 (ERROR):** `set(EXPOSE) == required`.
  - **L051 (ERROR, security, advisory-over-the-real-gate):** `set(EXPOSE) ∩ forbidden == ∅`
    — "never EXPOSE a port that no entry proxies." Per Binding condition 1, this static
    pass is explicitly *advisory*; the bind smoke-check is the gate.
  - **L052 (WARN→ERROR after migration):** image bakes no default.
  - Mutant tests: proxied+equal-port-tab on the same external must PASS EXPOSE of that
    port; equal-port-only must FAIL; pure internal must FAIL; plus the aio-studio
    selkies-strip mutation case.
- **Generator:** scaffolds a matched `EXPOSE` + `PORTAL_CONFIG` pair with `CHANGEPORT`
  markers and a required explicit declaration for any equal-port/direct entry.
- **Migration (monotonic; per-image/family; never base-inherited-to-all-33-at-once):**
  (1) land parser + L050/L051 dormant + L052 WARN;
  (2) backfill `EXPOSE` on the 9 baked images, auditing each entry against the app's
  actual launch `--port`/`HOST`;
  (3) backfill default + `EXPOSE` on the 24, batched by build family, each with a
  `docker build` + boot smoke test;
  (4) flip L052→ERROR once all bake a default; add the regression-net assertion.

## Binding conditions

Surviving conditions from the final review gate. If any is refused, the decision is
void (it would manufacture a green check that ships the loopback-exposure regression
it claims to prevent):

1. **The `0.0.0.0` bind check is a MANDATORY smoke gate, not "static-where-visible."**
   For every image the boot smoke test runs an in-container `ss -ltnp` and FAILS if any
   `EXPOSE`d port — *including equal-port siblings sharing that external* — has a
   `0.0.0.0`/`::` listener. L051's static pass is downgraded to advisory; this is the
   real gate. (Binds are routinely not statically visible: `npm run start`,
   `acestep-api --port 8001`, `${WAN2GP_PORT}`.)
2. **`portal.py` extracts `PORTAL_CONFIG` including the env script's post-assignment
   mutations** (e.g. aio-studio's selkies `:16100:` strip), pinned by a regression test
   on that case. If infeasible, re-scope to lint the **rendered runtime config**
   (a `portal.yaml` dumped during the smoke test), not the at-rest string — and say so.
   The "validates the at-rest baked default" claim is not safely implementable as first
   written.
3. **The entry-audit reconciles each `PORTAL_CONFIG` internal port against the app's
   actual launch `--port`/`HOST`**, treated as a found-bug fix with its own test (ACE-Step
   `:3000` vs `--port 8001`; Wan2GP `7861:7861` vs `17861`). Because "fixing" an entry to
   `ext≠int` is what arms the exposure, condition 1 and condition 3 must land **together**
   — never the audit first.
4. **Remove the hardcoded `{1111,8080}` base-allowlist, or justify it with a test** that
   the platform guarantees those maps. Otherwise a host that does not auto-open 1111
   leaves the Instance Portal unreachable while EXPOSE of 1111 is forbidden.

Additionally documented (not gates, but must be stated so a green L051 is not
misread): **L051 proves "proxied," not "authenticated"** — a runtime `AUTH_EXCLUDE`
can mark a proxied port no-auth, outside the linter's view; and the linter validates
the baked default, not the post-`10-prep-env.sh`-mutation runtime string. The smoke
test must `rm /etc/portal.yaml` first (the runtime prefers that cache over
`PORTAL_CONFIG`).

## Consequences

- **Positive:** template authoring needs less hand-typing (EXPOSE auto-requests the
  port that makes Caddy proxy it); images self-describe their port map; the cross-check
  is a regression net over all images; fits the existing linter discipline with minimal
  new machinery; both encodings stay greppable.
- **Negative / accepted:** two encodings persist, so drift is *detected* not impossible
  (mitigated by L050 in CI); a second `PORTAL_CONFIG` parser must track the runtime one
  (mitigated by a pinned test); the real safety gate is a per-image GPU-class smoke test
  asserting loopback binds, which is heavier than a static check; `AUTH_EXCLUDE` and the
  64-port ceiling remain unmodeled (documented, not gated).

## What would reverse this

- If `portal.py` cannot reliably extract the effective `PORTAL_CONFIG` (condition 2
  infeasible) AND dumping a rendered runtime config in smoke proves impractical → the
  static cross-check is unsound; ship only the bind smoke-gate (condition 1) and drop
  L050/L051.
- If the mandatory `0.0.0.0` bind smoke-gate (condition 1) cannot be made to run for
  every family → do not add `EXPOSE`; EXPOSE without that gate is in direct tension with
  the loopback invariant and manufactures false confidence.
- If Vast's EXPOSE auto-map behaviour changes such that EXPOSE no longer drives
  `VAST_TCP_PORT_<ext>` → the friction-reduction rationale collapses; revisit.
