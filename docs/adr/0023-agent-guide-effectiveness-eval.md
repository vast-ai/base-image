# ADR 0023 — Quantifying the in-image agent guides: an SSH-driven, arm-paired effectiveness eval

**Status:** Accepted (conditional)
**Pilot Zero status (2026-08-11):** PZ1 (banner reaches a one-shot SSH agent) and PZ2 (recycle = same host, fresh container, boot chain re-runs) both PASS on live boxes — see `docs/forge/2026-08-11-agents-guide-demo-eval/6-pilot-zero-results.md`. Added design rule: re-read the external port map after every recycle (recycle remaps ports); launch only from the template, never bare `--ssh`.
**Date:** 2026-08-11
**Decision owner:** Rob Ballantyne

---

## Context

The base-image family ships an agent-enablement surface: per-image guides under
`/etc/vast_agents/` assembled into `/etc/vast-agents-guide.md`, symlinked as
`AGENTS.md`/`CLAUDE.md` into the landing directories, a pointer line appended to
the SSH banner, and a machine-readable capabilities manifest (`vast-capabilities`,
`/etc/vast_capabilities.json`, portal `GET /capabilities`). It accreted across
~10 weeks (PR #165 merged 2026-06-04, then #175/#176/#180–#184/#192/#193/#194) —
there is no single before/after release.

We need to demonstrate to internal technical stakeholders that this surface does
real work — that an AI agent operating a Vast instance succeeds and stays safe
more often *because* the surface is present — and to answer, quantitatively,
**"does it help, and how much?"** The audience can and will audit the method, so
the result must survive hostile review; "it looks nice" is not evidence.

This decision is the output of a full design review (idea gate → three blind
competing designs → blind judging panel → synthesis → final adversarial gate);
the raw material is preserved under
`docs/forge/2026-08-11-agents-guide-demo-eval/`.

Relevant prior art and invariants: the live-GPU QA machinery this eval reuses
([ADR 0005](0005-live-gpu-qa-gate.md) — launch/teardown, ledger, reaper); the
inadvertent-exposure gate ([ADR 0006](0006-inadvertent-exposure-gate.md)/`tests/base/28-inadvertent-exposure.sh`,
which only runs under `INSTANCE_TEST=true` and which this eval must not trip); the
public-repo redaction rule ([ADR 0012](0012-adr-location-and-content-guardrail.md), linter L060/L061 — no account
IDs, keys, or tracker ticket IDs in any repo file, this ADR included).

## Options considered

Three genuinely different designs were built blind and judged by a blind panel
(technical-feasibility / value / risk lenses). The panel did not converge — but
**every judge ranked the chosen axis second**, and the two designs that beat it on
some lens are the extremes this work is scoped to avoid.

- **In-container agent, fresh instance per trial (Design A).** Runs headless
  Claude Code *inside* the box. Highest internal validity and best blinding
  analysis, but with cwd `= ${WORKSPACE}` the guide is auto-loaded into the prompt
  before the agent acts — it measures *"does the pasted-in content help"*, not
  *"does the shipped discovery machinery get the guide read"*, and never tests the
  SSH banner. ~26.5 person-days, up to ~$1,900. **Rejected:** it answers the wrong
  question for an ephemeral-instance product, and an agent harness living on a
  throwaway box is the wrong deployment model.

- **Lean batched battery (Design C).** Nine tasks batched onto each box with a
  scripted revert between them; cheapest (~9 days, ~$250). **Rejected:** the panel
  (risk lens) proved against the repo that its arm-delivery wrote the arm label
  into `/etc/environment` and left the strip script in `/tmp` (unblinded on the
  primary endpoint), and that its control arm kept a live `/capabilities` manifest.
  Batching also introduces arm-correlated drift across the nine sequential tasks.
  Its best ideas (boolean-verifier scoring, explicit supportable/not-supportable
  claim wording, ADR-as-pre-registration) are grafted into the decision.

- **SSH-driven agent, arms paired on a recycled rental (Design B) — chosen axis.**
  The agent runs on the orchestrator and reaches the box only over SSH — the
  realistic "user's laptop + remote GPU" pattern, and the honest test of whether
  the guide is *discovered* over the wire. Arms T (shipped surface) / P (placebo
  nudge) / C (stripped) run back-to-back on the **same physical host** via Vast
  `recycle`, which removes host heterogeneity — the single strongest structural
  idea in the panel — at near-zero cost. Its factual homework was the best of the
  three (portal-patch payload verified byte-exact; two repo-tooling traps caught
  that the others walked into).

The user chose the SSH axis explicitly: *"I want to prove this over SSH only.
Agent harnesses don't really belong on the instance due to the ephemeral nature.
I also don't think we need to go absolutely nuts here — the question is 'does it
help? how much?'"*

## Decision

Build **Design B, hardened by the panel and the final gate, and right-sized to the
question.** Full plan: `docs/forge/2026-08-11-agents-guide-demo-eval/4-synthesis.md`
as amended by `4-synthesis-resolution.md`.

- **Three arms, current image, one frozen dated tag, identical template + port
  map.** T = shipped surface; P = guide/manifest stripped and replaced by a
  generic ~90-word "inspect before acting" nudge with zero Vast-specific content
  (isolates "told to be careful" from "the content works"); C = fully stripped.
  The genuine pre-#165 old build appears in the demo as **narrative only, no
  numbers**, with the confound list (`git log --since 2026-06-05 -- ROOT/
  portal-aio/`; #205 portal-crash fix, #214 thread caps, #204/#229 portal
  responsiveness) disclosed by us first.

- **Ports held identical across all arms** (a template property, not a per-arm
  gift); the self-mapped port is **72300**, not 72299 (which syncthing owns —
  verified against `ROOT/opt/supervisor-scripts/syncthing.sh:60`).

- **The strip removes description/discovery, never capability.** The guide,
  symlinks, banner line, `vast-capabilities` CLI/JSON and the `GET /capabilities`
  routes are removed in C/P; the **`POST /capabilities/provision` action API stays
  live in all three arms** and is asserted every trial. Stripping a capability, not
  just information, would be the same category of rigging as disabling the
  control's network.

- **Arm applied at boot via a stage-15 `HOTFIX_SCRIPT` payload** (not post-boot —
  see Binding conditions), from an **arm-symmetric fixed URL** (same string in
  every arm) served with a per-recycle nonce and 404-after-first-fetch, that
  self-scrubs its own residue and replaces the stage-80 assembler with an
  identical-output shim so the boot log does not differ by arm. Residual leak is
  arm-symmetric and **measured** by a blinded arm-guessing classifier
  (AUC vs. 0.33 chance), reported as a pre-registered secondary.

- **The agent runs containerised on the orchestrator**, reaching the box only over
  SSH, with a per-trial task-scoped key and no mount of the eval repo, arm scripts,
  or assignment file. Nothing of the harness lives on the instance.

- **Task suite (frozen before tuning):** 3 primary ops tasks — expose a web app,
  data persistence, the CUDA-driver trap — plus 1 context-tax control (debug a
  script) and 1 over-trust probe (a guide-silent topic: container thread/pids
  caps, ADR 0014). Truthfulness scored free on every trial from a required
  handover report. Outcomes on a 4-level scale {correct-and-safe, correct-but-
  unsafe, failed-safely, failed-dangerously} computed from **out-of-band, arm-
  neutral verifiers** (external curl with/without token; control-plane state;
  fresh-SSH inspection — never `vast-capabilities`).

- **Primary endpoint (one):** proportion **correct-and-safe**, pooled over the 3
  primary tasks, **T vs C**, as a matched (stratified-by-block) risk difference
  with a 95% CI. **33 primary matched blocks** (11 per task). Everything else
  (T vs P, the 4-level distribution, truthfulness, over-trust, context tax,
  cost-per-success) is secondary/estimation-only, never called "significant".

- **Pre-registration** = this ADR + a frozen protocol appendix merged to `main`
  **before trial 1**, SHA printed on the results table. Null result is in scope:
  the failure taxonomy feeds a guide-revision backlog. The number is stamped
  point-in-time (one agent, one model, one date) and is **not a KPI**.

## Binding conditions

Non-negotiable; the final adversarial gate
(`docs/forge/2026-08-11-agents-guide-demo-eval/5-redteam-gate.md`, verdict
PROCEED-WITH-CONDITIONS) refused the plan without these. If any is later refused,
this decision is void.

1. **Arm application, including the `/capabilities` route strip and the
   `/.first_boot_complete` freeze, MUST run at boot stage 15 — before the portal
   starts (stage 65) and before first-boot (stage 25).** The portal runs
   `fastapi run` with no hot-reload (verified: `portal-aio/launch.sh:44`,
   `ROOT/opt/supervisor-scripts/instance_portal.sh:11`), so an on-disk edit to a
   running portal does nothing; a post-boot strip would either discard every C/P
   trial or leave the control serving a live capabilities manifest. Pre-flight
   must re-validate, live, every trial: `GET /capabilities` → 404 in C/P and 200
   in T, `POST /capabilities/provision` → 422 in all three, the auth edge back up.

2. **The boot-time leak class MUST be closed to arm-symmetric residue, and the
   residual MUST be measured.** Arm-symmetric fixed `HOTFIX_SCRIPT` URL; payload
   self-scrubs `/tmp/hotfix.sh`, the hotfix log, and its `/etc/environment` line;
   stage-80 replaced by an identical-output shim so the boot log does not differ
   by arm; blinded arm-guessing classifier reported. If the classifier beats
   chance, the result is reported as confounded by unblinding.

3. **Power and CI stated honestly.** 33 matched blocks resolves ~30 pp at
   discordance ψ≈0.40 (widening to ~34 pp at ψ=0.50) — stated verbatim in the
   pre-registration and on the slide. No task-cluster bootstrap (degenerate at 3
   tasks); the CI is a block-level bootstrap, and a leave-one-task-out sensitivity
   shows whether one task carries the effect.

4. **PZ1 gates all build spend:** verify live that sshd presents `/etc/banner` on a
   one-shot `ssh host cmd` and that the text reaches the agent's transcript. The
   repo does not configure this (control-plane owned). If it fails, **stop and
   ship that as the finding** — "the one channel built to be unmissable misses the
   SSH-agent pattern" — do not fake the result. PZ2 (recycle preserves host/ports,
   wipes container) is the second hard blocker.

5. **A self-destruct voids the whole block, not one arm.** An agent that stops or
   destroys its own rental is scored failed-dangerously and the block is re-run
   whole on a replacement rental; the partial block is dropped.

6. **Safety / blast radius.** Operator-invoked only, never a CI gate; deliberate
   exposure lasts only the trial window and is torn down; no real credential on
   any box (dummy HF token only); layered teardown (ledger-on-create, per-trial
   watchdog, hard per-rental deadline on a second machine); repo redaction rules
   (ADR 0012, L060/L061) apply to the pre-registration and results.

7. **The agent runs under bypassed permissions** (`--dangerously-skip-permissions`)
   — inherent to autonomous-agent evaluation, accepted here because boxes are
   disposable, hold no real credentials, and are destroyed within the trial
   window. Recorded as an accepted risk, not hidden.

## Consequences

- **Positive:** a defensible, pre-registered answer to "does it help, how much?"
  for the true ephemeral-SSH usage pattern, at ~2 weeks and ~$300–450, stamped
  point-in-time. A near-null is a publishable finding, not a failure. The recycle
  characterisation (PZ2) and the arm-symmetric boot-payload technique are reusable
  by the repo's existing QA tooling. The failure taxonomy directly seeds guide
  edits (each a separate vetted change).
- **Accepted-negative:** measures the **SSH discovery channel only** — the
  landing-dir auto-load path is out of scope, disclosed, not hidden. Powered for a
  **large effect (~30 pp)** only; a modest effect returns a CI that includes small
  values, pre-committed in the null wording. One agent, one model, one date; no
  generalisation to "AI agents" and no KPI. P and C are synthetic builds that never
  shipped. The volume-persistence branch of the persistence task is disclosed as
  untested, not simulated.

## What would reverse this

- PZ1 fails (banner does not reach an SSH agent) → the SSH axis measures nothing;
  pivot to reporting the banner-delivery gap as the finding, or re-scope to the
  in-container axis (a different ADR).
- PZ2 fails (recycle does not preserve host/ports or does not fully wipe) → the
  matched-pairs design collapses to fresh-rental-per-trial at ~3× cost and loses
  the matched analysis; re-decide scope before spending.
- The arm-guessing classifier beats chance materially → blinding failed; the
  primary result is confounded and must be reported as such, not as a clean effect.
- A stakeholder withdraws the bypassed-permissions authorisation (condition 7) →
  the autonomous eval cannot run as designed.
