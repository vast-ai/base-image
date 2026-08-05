# ADR 0018 — QA-gated, human-approved base-image `-auto` promotion

- **Status:** Accepted (conditional — see Binding conditions; build sequenced from condition 1)
- **Date:** 2026-08-05
- **Decision owner:** Rob Ballantyne
- **Extends:** [ADR 0005](0005-live-gpu-qa-gate.md) (live-GPU QA gate). Where this ADR
  admits the base image to the gating allowlist and adds conditions 12–15, it extends
  0005 explicitly; nothing in 0005 is silently overridden.
- **Process:** idea brief → critical-review gate (which cut an upstream-version-detection
  component after measuring its value case) → three competing designs → independent
  scoring on feasibility/value/risk → synthesis → final critical review of the written
  plan (verdict: serious-but-fixable; every surviving finding folded into the Decision
  and Binding conditions below).

## Context

The base image is the highest-blast-radius artifact in this repo: every derivative
`FROM`s it directly or via pytorch, and the patch-versioned `cuda-X.Y.Z-auto` prod tags
feed Vast's `@vastai-automatic-tag` backend used by live customer templates. Until now
**nothing tested the base image at any point**: `build-base-image.yml` (dispatch-only)
pushes 12 configs × 5 pythons × 2 arches to dated staging tags with zero tests, and
`promote-base-image.yml` (dispatch-only, `environment: production` with required
reviewers) crane-retags a whole staging date to prod and repoints every auto tag via a
two-phase "dance" whose push order the Vast backend depends on.

Two structural facts shaped the design:

- **The vLLM pattern does not transfer.** In `build-vllm.yml`, QA sits between build and
  the production write *in one run*. Base promotion is a separate workflow, dispatched
  days later, keyed by a **mutable** date tag (same-day partial re-dispatch rewrites it),
  and CI artifacts expire in one day — so any cross-workflow "QA passed" record that is
  not digest-bound can certify bytes that are no longer what gets copied.
- **ADR 0005's allowlist principle** forbids presenting boot-and-curl as "QA passed".
  0005 rejected boot-level gates because they launder "booted" into "QA'd" *for the
  product above the boot*. For the base image there is no product above the boot: the
  platform layer (supervisor, portal, Caddy TLS/auth, the exposure gate, python/venv,
  CUDA userland vs driver) **is** the product, and `ROOT/opt/instance-tools/tests/base/`
  exercises it — including real driver-API CUDA work (`cuInit`/alloc via ctypes, library
  loads). Base therefore earns a legitimate allowlist seat **only if** the suite cannot
  silently skip: today `60-gpu-cuda`/`61-cuda-compute`/`62-gpu-libraries` self-skip
  (exit 77) on a GPU-less or driver-broken box and the runner reports green — the exact
  skip-as-pass class 0005 documented for `vllm.d`.

Constraints carried throughout: the QA account is single-key and 429-prone with no
account-level semaphore yet (0005 cond 6); the owner requires **all 12 configs** tested
per promotion (bounded parallelism, not a sample) and `-auto` promotion to remain
**always human-approved**; the batch promotion model (one staging date, one approval) is
preserved; upstream version detection was cut from scope after review showed 11/12
configs already at their minor's terminal patch (~4–8 automatable events/year) and the
existing detection primitive cannot even see 11 of the 12 tracked minors.

## Options considered

- **A. QA at promote time, in the same run as the prod write (chosen).** The promote
  workflow resolves the digests it is about to copy, live-GPU-tests them pre-approval,
  and the human approves with the evidence table in view. **Why it won:** same-run
  evidence structurally deletes the cross-run trust problem (no evidence carrier, no
  schema, no stale/forged/retried-until-green record can exist); smallest delta to a
  working system (build workflow untouched; the dance changes by one ref substitution);
  the only rollout that is enforcing from birth rather than metered over promotion
  cycles a two-person team will not sustain.
- **B. QA at build time + a digest-keyed attestation artifact in the registry, verified
  fail-closed at promote.** The strongest single mechanism reviewed (a positive
  digest-keyed pass record is immune to every absence-of-red failure mode, and handles
  same-day partial rebuilds with no special-casing) and the best floor reasoning.
  **Rejected for v1:** the largest permanent surface (a payload schema to version, a
  five-module tool, a fourth workflow, never-GC'd artifact tags, registry write
  credentials granted to the shared `qa-gate.yml` that has never held any); an
  unbounded retry-until-green residual (a maintainer can re-dispatch the standalone QA
  workflow until a flaky cell passes, invisibly to the approver); and a multi-phase
  rollout keyed to promotion cycles that at this cadence realistically never reaches
  enforcement. The risk reviewer **dissented in favour of B** (positive-artifact
  predicate; flake measured before enforcement; zero-cost rollback) and named the
  condition that closes the gap for A: a rollback path — adopted (see Decision).
  Recorded as the evolution path if promoting older already-QA'd dates becomes routine.
- **C. Collapse build+promote into one release pipeline.** Contributed four ideas the
  decision adopts (config-table extraction, a dance-only rollback mode, the
  flip⇒evidence predicate, the pre-committed de-scoping fallback). **Rejected as a
  whole:** it rewrites and deletes both working workflows — including the dance, whose
  ordering semantics depend on an untestable external backend — in one move; it shipped
  a one-checkbox QA bypass (`QA=skip`) and an "INERT" pass-on-no-offers verdict whose
  premise is false (QA's `no_offers` reflects QA's own cost/reliability filters, not
  customer serveability — customers face none of those filters), i.e. two designed-in
  ways to flip an auto tag to untested bits; and its hand-maintained auto-version field
  was shown to rename the customer-facing auto tag undetectably (its own worked example
  would have stranded `cuda-13.3.1-auto`'s audience).
- **CPU-container harness only (no live GPU).** Only 3 of the 24 base tests strictly
  require a GPU. **Rejected as the whole answer** (as in 0005): the live boot also
  validates portal/Caddy/supervisor/exposure under real Vast driver injection per
  config, which is precisely the surface recent field defects came from. Revisited only
  as the degraded mode of binding condition 15.
- **Atomic batch gating (any red cell blocks the whole promotion).** The original shape
  of option A. **Superseded during the final review:** with 12 cells the gate's success
  is pⁿ in per-cell reliability (≈54% clean-run at an optimistic 95%/cell), which
  predictably drives operators to route around the gate. Chosen instead: flip-passing /
  hold-failing (Decision), which keeps every flip tested while making one flaky cell
  cost one held tag instead of a multi-hour re-dispatch.

## Decision

Wire a live-GPU QA phase into `promote-base-image.yml` itself, pre-approval, and make
the prod write digest-pinned and evidence-verified. The enforced invariant:

> **No `cuda-X.Y.Z-auto` tag flips to a digest whose amd64 manifest has not passed a
> live-GPU QA run of those exact bits in the same workflow run that writes it, and no
> prod write happens without an `environment: production` human approval given after
> the QA evidence is visible.**

Shape:

- **Job graph:** `preflight` (fail-closed on missing `VAST_API_KEY` + all DockerHub
  secrets) → `generate-configs` (from a single extracted config table) →
  `resolve-digests` (existence pre-flight moved before approval; `crane digest`
  manifest of record; pre-capture of every auto tag's digest; **immutable run-scoped
  alias tags** `qa-<run_id>-<config>` published in staging at the exact index digests
  the auto tags will hold; `PYTHON_VERSION` provenance assertion) → `qa` (matrix ×12
  via the extended reusable `qa-gate.yml`: `require_key: true`, `timeout: 2400` **plus**
  `PROV_TIMEOUT/INSTANCE_TEST_DEFAULT_TIMEOUT/VLLM_HEALTH_TIMEOUT=900` in `extra_env`
  — the client only ever lifts a too-short `--timeout`, so both halves are required;
  `retries` on `no_offers` only, 5–10 min jittered backoff, wall-clock deadline guard;
  `fail-fast: false`, `max-parallel: 2`) → `qa-summary` (12-row evidence table into the
  job summary + Slack ping; does **not** fail the run on a red cell) → `promote`
  (`environment: production`): `verify-evidence` (every auto tag that would flip must
  be covered by a passing evidence record at the matching digest; alias tags
  re-resolved; concurrent-promotion abort on moved auto tags) → copy **by digest** →
  dance with digest-ref targets, Phase A/B ordering and the full-config sweep
  byte-identical.
- **Partial failure: flip passing, hold failing.** Auto tags with passing evidence
  flip; failing/inconclusive configs keep their pre-captured digest (the dance
  re-pushes them unchanged, preserving ordering) and are named loudly — verdict class
  and held-digest age — in the approval summary and Slack. Dated-tag copies proceed for
  all configs (dated tags are opt-in by explicit reference; the gate's claim is the
  `-auto` surface). There is no `EXCLUDE` input and no `SKIP_QA` input.
- **Fail-not-skip:** the in-instance runner gains a required-pass gate
  (`INSTANCE_TEST_REQUIRE_PASS`: a named test that is skipped, **missing**, or
  unreached marks the suite failed), and CI independently re-asserts the per-cell
  required set from the machine-readable verdict before accepting a pass. The required
  set is defined **per cell in the workflow matrix** (cuda configs: the GPU trio;
  stock configs: the non-GPU subset, stated plainly as CPU-verified) and pinned by a
  guard test; the template's baseline env is defense in depth (linter rule L057).
- **Floors:** one lintable QA template (`templates/base-qa/`), `compute_cap >= 750`,
  per-GPU `gpu_ram >= 8192`, `reliability2 >= 0.95`, and **major-baseline**
  `cuda_max_good` (11.8 / 12.0 / 13.0): within a CUDA major, minor compatibility is
  guaranteed and the suite's own compat branch passes same-major skew, so
  major-baseline is both representative (a 13.0-driver host legitimately serves 13.3
  bits) and keeps the offer pool wide. CI tightening of floors is **raise-only**
  (`create.py --set-filter`), preserving the linted floor guarantee at rent time.
- **Rollback:** a separate tiny dispatch workflow runs **only the dance** against
  already-promoted prod dated tags of a prior staging date — no build, no copies, no
  QA, seconds, still `environment: production`, and **in the same
  `base-image-promote` concurrency group** as promote so the two dances can never
  interleave. Safe because it can only repoint auto tags at bits that already went
  through promotion; rolling *forward* untested bits remains impossible.
- **Config table extracted** to a single machine-readable file consumed by both
  workflows (matrices proven byte-identical before anything else moves). The auto-tag
  version stays **derived** from `tag_template` (no hand-maintained field — a second
  source of truth for a customer-facing name was shown to be a silent-rename hazard),
  with a unit test asserting every cuda config yields a version and stock configs none.
- **Linter:** L050/L054 base-class exemptions removed (no-op on today's baseline);
  new L057 for the base QA template; `docs/lint-rules.md` regenerated; mutation tests
  for every new check, including one that pins today's skip-as-pass bug before fixing it.
- **Sequenced prerequisites:** (0) get current — bump the one stale config
  `cuda-13.3.0 → 13.3.1` in both workflows and rebuild/promote once on the existing
  path; (1) fix `imagegen-tests.yml` so `tools/template_manager/tests/` actually runs
  in CI (it never has — pytest is rooted at `tools/imagegen`, so 0005's own named
  pre-merge guard has never executed); then harness+linter, config extraction,
  gate parameterization, template+floors, the condition-10 floor sweep, wiring, and
  staged first exercises (dry-run → full unapproved run against the live date →
  idempotent approved re-promotion of the live date → first real promotion).

## Binding conditions

Non-negotiable; each needs its enforcing artifact (0005 cond 11 applies). If any is
refused, this decision is void.

1. **The CI test-collection fix lands first.** Every guard and mutation test below is
   decorative while `tools/template_manager/tests/` is uncollected. Artifact: the
   updated `imagegen-tests.yml` plus a green run showing the suite executing.
2. **(extends 0005 as cond 12) Flip ⇒ same-run digest-verified evidence.** An auto tag
   may only flip to a digest whose amd64 manifest is covered by a passing,
   digest-matching QA evidence record produced in the same workflow run. Artifact:
   `verify-evidence` unit tests including the mutation case (evidence at digest D,
   promote about to flip to D′ → refuse).
3. **(extends 0005 as cond 13) No bypass, no schedule, fail-closed.** The promote path
   has no soft-pass, no bypass input, and no `schedule:` trigger; a missing
   `VAST_API_KEY` fails the run before any spend. The only un-QA'd prod write is the
   rollback workflow, which can only repoint to already-promoted bits and shares the
   promote concurrency group. Artifacts: dispatch-inputs, trigger, needs-edge,
   concurrency-group and `require_key` guard tests.
4. **(extends 0005 as cond 14) Pre-committed de-scoping fallback.** If more than 1 in 3
   promotions hold on infrastructure (not real image defects), the gating set is cut to
   a representative 4 (oldest CUDA, newest CUDA, each ubuntu base) and the rest labeled
   non-gating — honestly, by ADR amendment, never by per-run exemptions.
5. **(extends 0005 as cond 15) Coverage is stated, not implied.** amd64 manifest only
   (the promoted index's arm64 child is never booted); default-python index only (the
   exact `-auto` surface); mini variants and non-default pythons promote untested via
   dated tags; single-GPU; no pre-Turing. The approval summary and the ADR say so.
6. **Fail-not-skip proven by mutation.** The GPU-less-box case turns the suite red under
   the required-pass gate (and stays green-skip without it); required-test
   missing/unreached cases covered; per-cell matrix required-sets pinned by a guard
   test. No mutation test, no gate.
7. **Floors validated by real runs before gating** (0005 cond 10): one recorded passing
   run per config at its declared floors against an already-promoted staging date.
8. **Recovery mechanics verified:** alias tags carry no `run_attempt`; cleanup deletes
   them only after successful promote (next run sweeps strays); "Re-run failed jobs"
   demonstrated to re-test the same pinned digest.
9. **Honest operating numbers:** ~2.5 h typical dispatch→approval at `max-parallel: 2`
   (6 waves), 4–5 h bad day; GPU ~$2.50 typical, $10 budgeted, `max_price 1.00`. The
   account-level semaphore (0005 cond 6) remains deferred; promotions are staggered off
   the other gates' cron edges, and the semaphore is revisited at the next image
   onboarding.
10. **Known carried defect recorded:** the dance's Phase A points each auto tag at
    another config's (QA-passed) image for a seconds-long window — pre-existing,
    unchanged in v1 to keep the dance byte-identical. Named follow-up: prefer the same
    config's previous digest as the Phase A anchor, foreign anchor only as fallback.
11. **ADR 0010 exemption is explicit.** Base cannot satisfy 0010's one-template rule:
    its customer launch templates live on the Vast side, outside this repo.
    `templates/base-qa/` is an approximation of a customer launch; its drift from the
    real Vast-side template is a stated risk no in-repo check can detect, and "QA
    passed" claims are worded accordingly. This is an exemption recorded here, not
    silent drift.
12. **Operational assumptions carry their probes.** The Vast automatic-tag backend
    resolving by newest push per CUDA version (which makes patch-bump auto-tag renames
    and historical orphans like `cuda-12.8-auto` harmless) is an owner-confirmed
    operational assumption, not a verified fact: the phase-0 bump includes a post-promote
    probe that new instances resolve the renamed tag before orphan cleanup. ADR 0005's
    stale "all qa jobs share the `concurrency: qa-vast-account` group" sentence (its
    rollout section contradicts its own Decision) is corrected as part of this work.

## Consequences

- The highest-blast-radius artifact in the repo stops promoting untested: every `-auto`
  flip is backed by a live-GPU run of the exact amd64 bits, the reviewer approves with
  the evidence in view, and a red or thin-market cell holds one tag loudly instead of
  blocking the fleet or shipping untested.
- Incident response gains a seconds-fast, approval-gated rollback that cannot ship
  untested bits — removing the pressure that historically deletes gates.
- Promotion becomes a same-half-day operation (~2.5 h to approval) instead of
  same-hour; accepted against the blast radius, with the rollback path keeping
  emergencies fast.
- Accepted-untested surface remains and is stated: arm64 children, mini variants,
  non-default pythons, multi-GPU, pre-Turing, and the Phase A window (condition 10).
- Standing surface: one QA template + floor table, a verdict/manifest/evidence toolset,
  four `qa-gate.yml` inputs (defaults preserve the existing consumers byte-for-byte),
  two linter rules + two exemption removals, one config file, one rollback workflow,
  and guard tests — all of it only trustworthy because of condition 1.
- The approval reviewers' environment protection is the load-bearing human gate and
  lives in GitHub settings, observed by no repo artifact. Owner decision (2026-08-05):
  prevent-self-review stays off (a dispatcher may approve their own promotion — the
  evidence table, not a second human, is the check), and no additional QA-account
  spend-ceiling work is taken on beyond the existing capped balance.

## What would reverse this

- Promoting older, already-QA'd staging dates becoming routine (not exceptional) — the
  same-run topology then taxes every re-promotion with a fresh sweep, and option B's
  digest-keyed attestation carrier becomes the right evolution; this ADR records it as
  the named successor design.
- The gate holding on infrastructure at the condition-4 threshold — triggers the
  de-scope to a representative 4, and if even that flakes, collapse toward the
  CPU-harness + minimal-GPU-cells shape rather than keep a red check nobody trusts.
- Vast shipping scoped keys, first-class ephemeral instances, or an in-repo view of the
  live launch templates — each simplifies (the reaper/concurrency posture, or the
  condition-11 exemption) without changing the gate's principle.
