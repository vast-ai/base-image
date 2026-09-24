# ADR 0047 — A dispatch may raise an engine's QA floors only for a custom tag, visibly, with a validated price ceiling

- **Status:** Accepted (conditional)
- **Date:** 2026-09-24
- **Decision owner:** Rob Ballantyne
- Related: ADR 0005 (live-GPU QA gate: test the smallest viable box), ADR 0019
  (raise-only floors, all-or-nothing promotion), ADR 0029 (a failing cell redraws on
  another host), ADR 0031 (inference images gate on contract, worker and capability);
  PR #270; [docs/invariants.md](../invariants.md) ("Name the BRACKET, not one arch")

## Context

`external/vllm/templates/vllm-qa/template.yml` sets the QA selection floor every vLLM
build is tested against: `compute_cap.gte: 800` (sm_80), commented as matching the
production vLLM floor. It is right for a mainline vLLM release. It is wrong for a
per-model build whose kernels exist only for a newer architecture.

Measured on `hy4-preview` (run 33509520098, a Blackwell-targeted build): three draws
landed on an A10, an RTX 3080 and an RTX 4000 Ada (cc 860, 860, 890). All three failed
with `no kernel image is available for execution on the device`, on both CUDA variants.
Everything that did not need the engine's kernels passed. A redraw (ADR 0029) cannot
help, because it excludes the failed machine and draws again from the same pool.

Two facts about offer selection shape the decision. Both are verified against
`tools/template_manager/test_template.py` and `qa-gate.yml`:

- **Offers are not picked cheapest-first.** `make_offer_sort_key` ranks by VRAM
  overshoot, then by the lowest `compute_cap` at or above the floor (ADR 0005: a pass at
  the floor generalises upward), then by GPU count. Price is only a cap
  (`dph_total.lte`). So the gate *deliberately* draws the oldest generation that meets
  the floor, and raising only the lower bound draws the cheapest-VRAM card above it.
- **The price ceiling can be removed by accident.** `qa-gate.yml` pastes
  `${{ inputs.max_price }}` straight into a `run:` script. `test_template.py` applies
  the cap with `if args.max_price:`, so `0` removes it entirely, and nothing bounds it
  from above.

`qa-gate.yml` already accepts `set_filters`, and `create.py` enforces it as raise-only
(ADR 0019). Today every caller passes a value committed in the workflow matrix
(`promote-base-image`, `promote-pytorch`, `build-llama-cpp`), so every floor that
reaches the gate has been reviewed. PR #270 proposed exposing it, plus `max_price`, as
free-text `workflow_dispatch` inputs on `build-vllm.yml`. That would be the first time
an **unreviewed, typed-at-dispatch** value reached the gate that decides promotion.

A design review of PR #270 (2026-09-24, four independent reviewers) agreed that the need
is real and the default path is inert. Its surviving concerns are recorded below.

## Options considered

**A. Merge PR #270 as written. Rejected.** It has four problems:

- **It is a route around QA.** Nothing ties the override to a per-model tag. A mainline
  release dispatched with `compute_cap.gte=1200` is tested only on Blackwell, then
  `merge-manifests` publishes it to `<version>-cuda-*`. Customers rent that tag under the
  sm_80 production floor. Raise-only stops QA *widening* past the linted floor. It does
  nothing about QA *narrowing* below what production promises, which is the risk that
  matters here.
- **The override is invisible.** The Slack headline still reads "promoted — live-GPU QA
  passed". The approver of the `production` environment is not shown the floors, and
  build-vllm uploads no verdict evidence.
- **`max_price` is unsafe as free text.** It is a script-injection sink in a job holding
  `VAST_API_KEY` (only people with write access can dispatch, which limits but does not
  remove the risk). `0` removes the cap, and a malformed value exits 2, which the retry
  loop reads as "no offers" and wastes three backoffs reporting the wrong cause.
- **Its own guidance repeats the failure.** `compute_cap.gte=1000` alone draws a
  consumer sm_120 card before any sm_100 one, so an sm_100-only image fails every draw
  exactly as hy4 did.

**B. Close PR #270; per-model builds that need newer hardware are not QA'd by this
gate. Rejected.** The need recurs with every architecture-specific preview build. The
alternative is what already happened: `vastai/vllm:hy4-preview-cuda-*` were published
from a run whose QA never exercised them on hardware they support, and they remain
unverified.

**C. Commit the raised floor to a per-model QA/production template, with a lint rule
tying the published tag's template floor to the floor QA used. Deferred, not
rejected.** This was the strongest dissent in the review, and it is structurally right.
The failure on hy4 was *correct*: an image with no sm_80–sm_89 kernels breaks the
contract the sm_80 template states. So the durable fix is a template whose declared
range matches the image's actual kernel set, read from the artifact as the invariants
require. It was deferred because it needs a per-model template class and a new
linter rule, which is disproportionate to the one-off preview builds that need it
today. Condition 1 below contains the risk C addresses in the meantime.

**D. Accept the override as a scoped, visible, validated escape hatch. Chosen.**

## Decision

Engine build workflows may accept dispatch-time QA selection overrides, starting with
`build-vllm.yml` (PR #270), **only** under the binding conditions below. With both
inputs empty, including on every scheduled run, behaviour is unchanged.

## Binding conditions

If any of these is refused or later removed, this decision is void and the override
inputs must be removed.

1. **Only with a custom tag, only on `compute_cap`.** Preflight refuses a non-empty
   override unless `CUSTOM_IMAGE_TAG` is set, so a mainline version or nightly tag can
   never be certified on narrowed hardware. The allowed keys are `compute_cap` only
   (`gte`/`lte`). Any other key is refused, because a new key or operator is accepted
   unchecked by the raise-only merge and could pin the gate to a single host
   (`machine_id`) or any one GPU class.
2. **Visible wherever a human reads the verdict.** When either input is non-default,
   the run's step summary and the Slack headline name the effective floors and price
   ceiling (for example "QA ran at compute_cap 1000–1030 only, max $12/hr"). A pass on
   overridden floors must never read the same as a normal pass.
3. **`max_price` is validated and cannot remove the cap.**
   - It reaches the `run:` script through `env:`, never an inline `${{ }}` splice.
   - Preflight validates it as a positive decimal under a documented hard maximum, and
     refuses anything else as a configuration error (not "no offers").
   - `test_template.py` treats a price of `0` or below as invalid instead of as "no
     cap".
4. **The guidance states the real mechanism.** Comments and input descriptions say
   offers are ranked smallest-VRAM, then lowest compute_cap at or above the floor, not
   cheapest-first. They document the range form (for example
   `compute_cap.gte=1000 compute_cap.lte=1030` for sm_100-only), since a lower bound
   alone draws the wrong Blackwell class.

## Consequences

**Positive.**
- A per-model preview build can be QA'd on hardware it actually supports, instead of
  failing every draw on hardware it never claimed, or being published untested.
- Mainline tags keep ADR 0019's guarantee unchanged: they can only be certified on the
  full linted floor.
- Validating `max_price` closes an injection sink and a silent removal of the cost cap
  that also affect the existing gate.

**Accepted negative.**
- **A custom tag certified on raised floors is still published under whatever template
  its users launch it with.** Nothing yet ties that template's floor to the floor QA
  used. Condition 2 makes the gap visible, not closed. Closing it is option C.
- **A raised `max_price` authorises spend up to the hard maximum.** That is multiplied
  across CUDA variants, both QA cells and the retry attempts. The maximum is the only
  bound.
- **The override is only as good as the bracket the operator types.** A wrong guess
  still produces a reproducible wrong-hardware failure, now at a higher price.

**Out of scope (recorded, not decided here).**
- The same inline-splice pattern exists in `qa-gate.yml` for `repo`, `tag` and `label`,
  and in `build-vllm.yml`'s build and `merge-manifests` jobs, which are fed from free
  text `DOCKERHUB_REPO` / `CUSTOM_IMAGE_TAG`. A workflow lint rule banning
  `${{ inputs.* }}` inside `run:` is the general fix. It is its own change.
- `parse_set_filter` accepts non-finite values (`nan`), and `for f in ${SET_FILTERS}`
  is unquoted. Both are low-risk hardening.
- Re-running `hy4-preview` from a newer nightly builds and tests a *different* image.
  The already-published `hy4-preview-cuda-*` digests stay unverified unless they are
  re-tested directly or removed.

## What would reverse this

- **Option C lands** (a per-model template class, plus a lint rule tying a published
  tag's template floor to its QA floor). The dispatch override then becomes redundant
  and should be removed.
- **An override is ever used to certify a mainline tag,** or a narrowed pass is ever
  announced as a normal one. Either means conditions 1–2 failed in practice, and the
  inputs should be removed until they are enforced by a test rather than by review.
- **Preview builds stop needing architecture-specific QA** (every custom build since
  hy4 has passed on the default floor). If that holds, the inputs are dead weight and
  can go.
