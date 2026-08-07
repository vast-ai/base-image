# ADR 0019 — QA-gated, human-approved base-image `-auto` promotion

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
  ways to flip an auto tag to untested bits (a guarded variant of its skip flag was
  later adopted by owner decision — see condition 3's amendment); and its
  hand-maintained auto-version field
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
  `-auto` surface). There is no `EXCLUDE` input; a loud, reasoned, human-approved
  there is no bypass at all (condition 3, as amended 2026-08-07 — the `SKIP_QA`
  flag added on 2026-08-05 was removed).
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
- **Manual auto-tag move:** a separate tiny dispatch workflow
  (`move-base-auto-tag.yml`) runs
  **only the dance** against already-promoted prod tags — no build, no copies, no
  QA, seconds, still `environment: production`, and **in the same
  `base-image-promote` concurrency group** as promote so the two dances can never
  interleave. Safe because it can only repoint auto tags at bits that already went
  through promotion; rolling *forward* untested bits remains impossible. As built it
  refuses any `TARGET` carrying a registry, namespace or repo prefix (so the source
  is always the production `base-image` repo), and refuses a source whose own
  `CUDA_VERSION` minor differs from the auto tag being written — putting a 12.4 image
  on `cuda-12.9-auto` is the single most damaging typo available at 3am, and it looks
  exactly like a correct command. It reports the digest it moved away from, because
  rolling forward again after the real cause is found should not require registry
  archaeology during an incident.
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
3. **(extends 0005 as cond 13) No silent or automated bypass; fail-closed.** The
   promote path has no soft-pass and no `schedule:` trigger; when QA is not skipped, a
   missing `VAST_API_KEY` fails the run before any spend.
   *(Amended 2026-08-05, then AMENDED AGAIN 2026-08-07 — owner decision. The
   condition originally read "no bypass input". The 2026-08-05 amendment added a
   `SKIP_QA` dispatch input under four guardrails, over the recorded objection that
   a skip input was the single most likely gate-erosion path — "used the first time
   the gate is inconvenient, then every time after". **That objection has now won:
   `SKIP_QA` is removed.** The condition reverts to its original form, and the
   removal is stronger than the original because it comes with a sanctioned
   alternative and executed tests.)*

   **There is no QA bypass. An image that does not pass QA does not reach an
   `-auto` tag via CI.** No dispatch input, no environment variable, no argument to
   `verify_qa_evidence`. Not a flag defaulting to off — absent, and asserted absent.

   What removed it was not the erosion argument but a concrete laundering path
   found in implementation review, and reproduced: the skip's untested-ness stuck
   to the RUN, not to the DIGEST. Dispatch with `SKIP_QA=true`, then immediately
   re-dispatch the same `STAGING_DATE` with `SKIP_QA=false`. By then prod holds the
   untested digest, so `target == current` for every config, the QA matrix is empty,
   the `qa` job is skipped entirely, and every row classifies
   `flip / "digest unchanged (re-push only)"` — zero holds, zero GPUs, no banner, no
   warning status, no "QA SKIPPED" in the run name. Cond 3's tripwire named the run
   history as its record; a two-minute sequence that relabels a skipped promotion as
   a gated one does not weaken that control, it removes it.

   **Holding costs less than it appears to, which is what makes removal affordable.**
   A held auto tag does not block shipping: the dated prod tags are promoted
   regardless of the flip/hold decision, so the bits are in production under their
   explicit names and only the customer-facing pointer waits for evidence. Verified
   behaviourally — with every config held, 12 prod dated tags still land and no auto
   tag moves.

   **The sanctioned alternative** for "verified by hand, QA cannot run" is the
   `Move Base Auto Tag` workflow (`move-base-auto-tag.yml`, renamed from
   `rollback-base-auto.yml` because it is now the forward path as well as the
   rollback path). Promote first, then move the tag there. It costs one extra
   dispatch and buys: a separate `production` approval, a separate run, a mandatory
   reason, a Slack line that says "MANUAL AUTO-TAG MOVE (no CI QA evidence)", a
   refusal of any source outside the production repo, and a refusal of a CUDA-minor
   mismatch. Crucially the record attaches to the tag move itself, so no subsequent
   promotion can relabel it.
   Artifacts: dispatch-inputs, trigger, needs-edge, concurrency-group, `require_key`
   guard tests, plus executed tests asserting that no env combination reaches
   "QA gate ready" without the key and that `classify_configs` has no skip parameter.
4. **(extends 0005 as cond 14) Pre-committed de-scoping fallback.** If more than 1 in 3
   promotions hold on infrastructure (not real image defects), the gating set is cut to
   a representative 4 (oldest CUDA, newest CUDA, each ubuntu base) and the rest labeled
   non-gating — honestly, by ADR amendment, never by per-run exemptions.
5. **(extends 0005 as cond 15) Coverage is stated, not implied.** amd64 manifest only
   (the promoted index's arm64 child is never booted); default-python index only (the
   exact `-auto` surface); mini variants and non-default pythons promote untested via
   dated tags; single-GPU; no pre-Turing. The approval summary and the ADR say so.

   *(2026-08-07, owner decision: the two largest of these gaps were reviewed and
   are DECLINED deliberately, not left open — see
   `docs/design/2026-08-07-qa-coverage-mini-and-arm64/`.*

   * ***mini images stay untested.*** They carry no `-auto` tag, so a bad mini
     image has no automatic path to a customer; it reaches one only via a
     derivative build pinning an explicit dated tag, which is a reviewed act.
     Extending flip/hold to dated tags was considered and rejected: it adds
     conditional behaviour to the job that writes production tags in exchange for
     covering artifacts with no automatic blast radius. Caveat of record: neither
     consumer (`llama-cpp`, `linux-desktop`) is itself QA-gated today, so the
     transitive coverage is a future property. Revisit if either ships to
     customers without its own gate.
   * ***arm64 stays untested, parked on market size.*** Measured 2026-08-07: 10
     offers at these floors versus 744 amd64, all one GPU model (GB10). A cell at
     that density would produce coverage debt rather than coverage. Re-open when
     arm64 offers at the base-qa floors reach ~50, or span more than one GPU model.
     Explicitly NOT adopted: any "test if available, else proceed" rule — that is
     the `SKIP_QA` defect in another costume, attaching untested-ness to the run
     instead of the artifact so it vanishes on the next dispatch.*

   The distinction that makes both acceptable: these gaps are **standing and
   disclosed**, restated at every approval, rather than per-run states that
   disappear.)
   *(As built: the summary renders a "What a `flip` here does and does not certify"
   section under the decision table, so the approver reads the limits in the same
   place as the verdicts rather than having to come here for them.)*
6. **Fail-not-skip proven by mutation.** The GPU-less-box case turns the suite red under
   the required-pass gate (and stays green-skip without it); required-test
   missing/unreached cases covered; per-cell matrix required-sets pinned by a guard
   test. No mutation test, no gate.
7. **Floors validated by real runs before gating** (0005 cond 10): one recorded passing
   run per config at its declared floors against an already-promoted staging date.
8. **Recovery mechanics verified:** alias tags carry no `run_attempt`; cleanup deletes
   them only after successful promote (next run sweeps strays); "Re-run failed jobs"
   demonstrated to re-test the same pinned digest.
9. **Honest operating numbers.** *(Corrected 2026-08-07 from the first full CI run,
   31170473940 — the original figures were estimates and were wrong by ~6x. They are
   replaced here rather than annotated, because a stale estimate in a condition is
   what the next person sizing this will trust.)*

   Measured, 10 cuda cells against staging date 2026-08-06 at `max-parallel: 2`:

   | | |
   |---|---|
   | QA phase, dispatch → approval requested | **~25 min** (estimated 2.5 h) |
   | per-cell duration | median **3.3 min**, slowest **13.6 min** |
   | total cell time | ~43 min |
   | HTTP 429s | **0** |
   | no-offer retries | **0** |
   | cells passing | 10 of 10 |

   `max-parallel` was raised 2 → 4 on this evidence. The ceiling is set by the tail,
   not by the rate limit: the slowest single cell puts a ~13.6 min floor under the
   phase, so 4 captures essentially all the available speedup (2→4 saves ~10 min;
   4→10 saves nothing measurable). It is not raised further because the cost is not
   only API pressure — concurrent cells query the same thin offer market, and two
   cells racing for one box surfaces as `bad_instance` (exit 3), which is
   deliberately never retried, so contention converts passes into holds.

   Ten minutes is in any case small against the human approval that follows, which
   is the real latency in this pipeline. Optimising the QA phase further is not
   where the time is.

   GPU cost tracked at ~$2.50 typical, $10 budgeted, `max_price 1.00`. The
   account-level semaphore (0005 cond 6) remains deferred and is the proper fix;
   promotions are staggered off the other gates' cron edges (comfyui and vllm at
   00:00/12:00 UTC), and the semaphore is revisited at the next image onboarding.

   **Caveat on generality:** one run, on one day, on a market that was not thin.
   The zero-429 result licenses 4, not 10, and says nothing about a bad day.
10. **Known carried defect recorded:** the dance's Phase A points each auto tag at
    another config's (QA-passed) image for a seconds-long window — pre-existing,
    unchanged in v1 to keep the dance byte-identical. Named follow-up with a **proven
    in-repo donor**: `promote-pytorch.yml`'s dance already fixes this exact hole
    (changed target → single direct copy, no hop; unchanged target → bump via the
    py310 sibling of the SAME image, so a mid-hop reader never sees wrong-CUDA bits).
    Port that pattern to the base dance as its own change after v1 lands.
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

## Implementation notes (added 2026-08-07, after the build review)

The build was reviewed as an implementation once complete. Two findings are recorded
here because they change what the conditions above are worth, not just the code:

- **The gate shipped disarmed, and every guard test passed anyway.** The hold check
  was written into the `dry-run` job instead of `promote`, so production would have
  flipped every auto tag with no reference to the QA decisions at all. The guard test
  intended to prevent exactly this searched the whole workflow file for the check's
  text, found the copy sitting in `dry-run`, and went green. A structural property
  needs a structurally *scoped* test: the tests now slice the `promote` job out of the
  file by name and additionally assert the check exists **nowhere else**, so relocating
  it fails the suite rather than passing it twice over.
- **A shared workflow can be broken by a change that looks local.** Making the verdict
  reach the shell via `eval` of a tool's stdout broke every QA cell for every consumer
  of `qa-gate.yml` — including the live vLLM and ComfyUI gates — because a *passing*
  verdict's reason contains parentheses, which is a bash syntax error under `set -e`.
  The verdict now travels through `$GITHUB_OUTPUT` as a heredoc and never reaches the
  shell as text to evaluate.

Both were caught by review, not by CI, and neither would have been caught by the
12 live-GPU validation runs — those exercised `qa-gate.yml` directly, never the
promote wiring around it.

A second review round then found three more of the same shape, two of them
introduced *by the fixes above*: the `dry-run` loop lost five assignments when the
misplaced gate block was removed from it; the new rollback workflow's version
regex accepted `12.9` and refused `12.9.2`, while every auto tag that exists is
patch-versioned, so the escape hatch was inert; and deleting the single word
`continue` from the hold branch flips every held tag, with all 19 guard tests
green.

The conclusion is about the tests, not the lines. Every safety property here is
bash inside YAML, and asserting that a *string* appears in that YAML cannot
distinguish a working gate from a disarmed one. So the wiring is now executed:
`tools/template_manager/tests/wfexec.py` extracts a step's `run:` script and runs
it under bash with a stub `crane` over a fake registry, and the tests assert the
digest each auto tag ends up holding. Of eight disarming mutations, six are
invisible to the string-matching suite and caught only by execution; the other
two are static consistency properties (the four copies of the required-test list,
and — until it was removed outright — the `SKIP_QA` default) now pinned
structurally.

This does not replace the structural tests — the job graph, the `production`
environment placement and the trigger set are still asserted against parsed YAML,
because those are not properties of any one script. The two layers fail
differently, which is the point.

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
- An owner-accepted bypass exists (condition 3, 2026-08-05 amendment): a promotion
  preceded by manual testing may skip the automated sweep. This is recorded against
  the final review's top-ranked erosion finding; the mandatory reason, the
  warning banner at approval, the distinct notification and the usage tripwire are
  the mitigations, and the tripwire is the tripcord.
- The approval reviewers' environment protection is the load-bearing human gate and
  lives in GitHub settings, observed by no repo artifact. Owner decision (2026-08-05):
  prevent-self-review stays off (a dispatcher may approve their own promotion — the
  evidence table, not a second human, is the check), and no additional QA-account
  spend-ceiling work is taken on beyond the existing capped balance.

## What would reverse this

- **A promotion being blocked with no way forward.** Holding is affordable only
  because dated tags still promote and `Move Base Auto Tag` exists. If operators
  start reaching for `crane` by hand instead, the gate has become the thing it was
  meant to prevent — revisit by ADR amendment, not by re-adding a flag.
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
