# ADR 0013 — Resolve the pytorch base tag from DockerHub, pin the result

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision owner:** Rob Ballantyne

## Context

A new pytorch-nested image `FROM`s `vastai/pytorch:<tag>`. Today the generator scaffolds
`ARG PYTORCH_BASE=vastai/pytorch:CHANGEME` and the `new-image` skill fills `CHANGEME` by
**copying the tag the closest sibling currently pins** (ADR 0001, skill Step 4). That is
unsafe: siblings drift. Ground truth today — every pytorch-nested sibling pins
`2.10.0-cu128-cuda-12.9-mini-py312-2026-06-15`, while DockerHub already publishes torch
`2.11.0` and will publish newer **dated rebuilds** of the same `(torch, cuda, py)` combo
(patched bases). Copying a sibling inherits whatever stale date that sibling last bumped to;
nothing re-checks DockerHub. There is no tooling to discover tags — the linter's L004 only
checks the base is *a* `vastai/pytorch` image, not that the tag is current or even exists.

The naive fix — "float the ARG to the latest matching tag at build time" — is **worse**: a
rebuild silently jumps to a base nobody tested this image against, and QA≠prod. ADR 0011
already rejects this for apps (`@vastai-automatic-tag` "auto-selects the newest, possibly
unvetted, tag"). The torch and CUDA versions are a **deliberate, safe** choice and must stay
explicit; only the **date** should be resolved — and resolved at a *controlled moment*, then
**pinned**, so builds stay reproducible.

## Options considered

**When to resolve:**
- **A. Resolve once at scaffold, write a concrete dated pin (chosen).** Probe DockerHub,
  pick the newest date for the chosen `(torch, cuda, py, variant)`, write the full tag into
  the Dockerfile ARG (+ CI matrix). Reproducible thereafter; QA==prod; re-run to bump.
- **B. Float the ARG to "latest matching" at build time. Rejected:** non-reproducible,
  silent base drift, QA≠prod — the exact risk pinning exists to prevent (ADR 0011).
- **C. Pin by default but allow an opt-in float. Rejected for now:** adds a second,
  unsafe code path and a way to get B by accident; no demonstrated need.

**Where the probe lives:**
- **D. Inside `imagegen new`, always. Rejected:** the generator is deterministic and
  offline (round-trip tests + the "pure-stdlib, no network" property depend on it); an
  unconditional network call breaks both.
- **E. A dedicated resolver primitive, consumed by opt-in callers (chosen).** A pure
  parse/select function (unit-tested offline against tag fixtures) behind a thin `urllib`
  fetch, exposed as: `imagegen resolve-base` (print), `imagegen new --resolve-base`
  (opt-in auto-fill instead of `CHANGEME`), and `imagegen bump <name>` (re-resolve an
  existing image). `new` with no flag still scaffolds `CHANGEME` and stays offline/deterministic.

## Decision

Add a **base-tag resolver** and wire it into scaffolding and a new `bump` command.

- **Inputs (the safe, explicit part):** `(torch, cuda-toolkit, py, variant)`. The `cu<wheel>`
  segment travels with the matched tag (cu128↔cuda-12.9, cu130↔cuda-13.2), so it is not a
  separate input. Defaults: `py312`, `mini`.
- **Resolution:** fetch all `vastai/pytorch` tags, parse each against the canonical scheme
  `<torch>-cu<wheel>-cuda-<toolkit>-[mini-]py<py>-<YYYY-MM-DD>`, filter to the requested
  tuple **and to index tags only** (no `-amd64`/`-arm64` suffix — that is what `FROM`
  resolves), and return the tag with the **max date**. Writing the result is a concrete pin.
- **`bump <name>`:** read the image's current pin(s), extract each `(torch, cuda, py, variant)`
  tuple, re-resolve to the newest date, and rewrite **both** the Dockerfile `ARG` default
  **and** every matching entry of the CI workflow's `base_image` matrix (an image may carry
  several, e.g. a cuda-12.9 and a cuda-13.2 row — bump each, preserving the set of combos,
  updating only dates). A bump is **not complete until the image is re-QA'd** — the skill
  drives the same build→live-GPU→iterate loop.

## Binding conditions

Non-negotiable; if any is refused the decision is void.

1. **The generator stays deterministic and offline.** The resolver is opt-in
   (`--resolve-base` / `resolve-base` / `bump`); `imagegen new` with no flag still writes
   `CHANGEME` and makes no network call. The generator round-trip tests do not touch the network.
2. **Fail loud, never silently stale.** A network error, rate-limit, empty result, or
   *no tag matching the tuple* is a hard error with a clear message (or, for `new`, a
   fallback to `CHANGEME` that says so) — the resolver must never return an older/wrong tag
   on failure.
3. **Resolve the multi-arch index tag**, not an arch-suffixed manifest — matching how `FROM`
   pulls. Arch-suffixed tags are filtered out.
4. **A bump is validated by CI, not a tooling gate.** A bumped pin is built and QA-gated by
   the existing `build-<name>.yml` pipeline (ADR 0005) when the change lands — `bump` adds no
   gate of its own, and this is safe because a published image cannot promote past a red QA
   gate. The `new-image` skill's scope is **create**; `bump` is a standalone maintenance
   command, not part of the scaffold loop.
5. **The tag scheme is parsed in one place, tested against real fixtures.** A scheme change
   upstream must fail condition 2 (no match → loud), not silently mis-resolve.
6. **Reproducibility is enforced.** Linter rule **L005** gates an app image (`pytorch-nested`
   / `derivative`) whose base **`FROM`** is not a concrete pin — i.e. `latest` or untagged (a
   dated tag or a digest passes). Apps pin; only `base-image`/`pytorch` may float (ADR 0011).
   Scope is the Dockerfile base only: `@vastai-automatic-tag` is a *template-tag* token, not a
   valid `FROM`, so it is out of L005's surface (a template-tag reproducibility check is a
   separate future rule). `RULES` code added, `docs/lint-rules.md` regenerated, baseline CLEAN.

## Consequences

- New images (and bumped ones) inherit the newest **dated rebuild** of their chosen
  `(torch, cuda, py)` combo automatically — the staleness fix — while every build remains
  reproducible because the resolved tag is written down, not floated.
- DockerHub becomes a **scaffold/bump-time dependency** (opt-in, fail-loud, ~12 anonymous
  API calls to page 1145 tags). It is never on the build or runtime path.
- `bump` gives a repeatable answer to the base-pin-staleness maintenance task (previously a
  manual per-image edit of two places).
- The resolver hard-codes the `vastai/pytorch` tag scheme; an upstream scheme change surfaces
  as a loud "no match," prompting a resolver update — not a silent regression.

## What would reverse this

- `vastai/pytorch` moving to a scheme the resolver cannot parse, with no stable replacement —
  fall back to manual pins.
- DockerHub anonymous tag listing becoming unreliable/rate-limited enough that resolution
  fails often — then cache or authenticate, or revert to sibling-copy.
- Vast publishing a first-class "current recommended base" pointer the resolver could read
  directly — simplifies (read the pointer), doesn't reverse the pin-the-result principle.
