# ADR 0030 — The derivative test phase does not start on a failed base

## Status

Accepted. Extends ADR 0029; changes nothing about when a cell redraws, only how
much it spends before it does. Recorded rather than bolted on because ADR 0029
binding condition 5 makes retry and verdict semantics a decision, not an edit.

## Date

2026-08-20

## Context

`runner.sh` discovers `base/*.sh` first, then every `*.d/*.sh` a derivative
image ships. A failing test sets `has_failure=true` and the loop **continues**;
only `test_fatal` (exit 2) aborts, and until now only `12-provisioning` used it.

Those two phases have very different costs. Measured across the 70 cells of the
2026-08-20 pytorch promote: the `base/` phase averaged **66s**, the `pytorch.d/`
phase **79s**. On pytorch the tail is barely worth discussing. On an image whose
suite downloads model weights and runs inference — comfyui, vllm, sglang,
wan2gp — the tail is tens of minutes against the same ~60s base.

That run also produced the case that prompted this. A harness defect in
`base/26-caddy-auth` (waiting on Caddy's admin port instead of the ports it
probes — see L071) failed a cell at position 26. The suite then ran every
remaining base test AND the whole derivative phase, finished, was reported
failed, and was redrawn on a fresh host. The expensive work was done twice and
kept neither time.

**ADR 0029 is what makes this newly wrong.** Under ADR 0019 a failed test meant
BLOCK, so finishing the run bought diagnostic detail before giving up. Under
0029 any failure redraws: the moment base goes red the cell is already lost, and
everything after it can only spend a rented GPU to reach a conclusion already
reached. Worse, a derivative assertion made on a platform whose base tests are
failing is not evidence either — a PASS there says little when the service stack
under it is sick.

## Options considered

### A. Leave it (rejected)

Cheapest, and it keeps the fullest possible report.

Rejected on cost, and only because ADR 0029 changed the trade: the run's extra
information cannot alter the verdict any more, so on a model-loading image it is
tens of minutes of rented GPU bought for nothing, twice — once in the doomed
cell and again in the redraw.

### B. Abort on the first failing test (rejected)

The obvious reading of "fail fast", and it saves the most.

Rejected because it destroys evidence that is nearly free. Base tests cost
seconds; finishing the phase costs almost nothing. And the diagnosis behind ADR
0029's amendment depended on exactly that: seeing `10-supervisor`, `20-portal`
and `26-caddy-auth` fail TOGETHER is what identified one shared root cause
rather than three faults, and seeing `67-service-functionality` pass on the same
endpoints 53 seconds later is what proved the host innocent and the harness
guilty. Aborting at the first red would have shown one line, and the honest
conclusion from it would have been "bad host" — which is what the original ADR
0029 concluded, wrongly, for three of its six exhibits.

### C. Retry the failing check in place (rejected)

Tempting for a flake, and rejected on principle: retrying a red until it passes
is the thing ADR 0019 said the gate must never do. Waiting correctly for a
readiness condition is legitimate and is what L069/L071 require; discarding a
failed assertion is not. A flake is a defect to fix, not a result to re-roll.

### D. Gate the phase boundary (chosen)

Finish `base/` in full; refuse to enter the derivative phase if it failed.

## Decision

**If any `base/` test has failed, the derivative phase is not started.**

1. **The whole base phase always runs.** Cheap, and it is where the diagnosis
   lives.
2. **Only a `base/` failure arms the gate**, tracked separately from the
   suite-global failure flag. The first implementation keyed on that global
   flag, which any failing test sets — so the second derivative test onwards
   tripped it, silently applying rejected Option B inside the very phase this
   ADR is about, under a banner blaming a green base. Co-failure in a `*.d/`
   suite is diagnostic for exactly the reason given above, and the expensive-tail
   argument does not apply once that phase has already started.

3. **The first non-`base/` test trips the gate.** Remaining tests are marked
   `skipped` and the suite stops.
4. **The report says the phase was NOT ATTEMPTED**, in those words, and states
   that the tests are neither passing nor skipped-by-design. A wall of `skipped`
   reads as benign; this is the one place that misreading would be expensive.
5. **The verdict is unchanged.** The failing base test is still recorded
   `failed`, so the cell fails on it and redraws under ADR 0029. The
   required-pass gate already treats a skipped required test as a failure, so a
   derivative test that never ran cannot be mistaken for one that passed.

## Binding conditions

1. **This must never turn a red into a green.** It removes work, not failures.
   Guarded by `test_a_base_failure_stops_the_derivative_phase` and
   `test_a_green_base_runs_the_derivative_phase_normally`.
2. **The base phase completes.** If a future change aborts earlier, the evidence
   that produced ADR 0029's amendment stops being collectable. Guarded by
   `test_the_whole_base_phase_still_runs_after_a_failure`.
3. **`skipped` must not read as passed** in the human-facing summary. Guarded by
   `test_the_report_says_NOT_ATTEMPTED_rather_than_letting_skip_read_as_pass`.
4. **A derivative failure must not abort the derivative phase.** Guarded by
   `test_a_DERIVATIVE_failure_does_not_stop_the_derivative_phase` and
   `test_the_banner_is_only_printed_when_BASE_actually_failed`. The guard tests
   for conditions 1-3 all shipped a single derivative test, which is why the
   first implementation passed them while doing the opposite of this decision.
5. **The tests assert the VERDICT, not the narration.** They read the exit code
   and `results.json`; asserting on banner strings alone let a mutation that
   cleared the failure flag pass every gate test.

## Note on the evidence for L071

The symptom this work chased — `expected 401, got 000` in `26-caddy-auth` — had
three causes attributed to it inside 48 hours (a bcrypt timeout, a 30s
`wait_for_caddy` budget, and finally the admin-port wait). Each was argued from
the same shape of circumstantial evidence, and two were wrong.

The discriminating datum is the ELAPSED TIME of the failing check, and it was
available the whole time. From the 2026-08-20 job log:

    10:28:47.6019052   FAIL: basic testadmin_2252 ... got 000
    10:28:47.6404190   FAIL: basic vastai:testpass (should reject) ... got 000

38 milliseconds apart. A curl timeout at `--max-time 20` would put them twenty
seconds apart; this is a refused connection, returning instantly — nothing was
listening. That is the bind race, not starvation.

The lesson is procedural and worth more than the fix: for a symptom with several
plausible causes, find the measurement that separates them before writing the
third explanation.

## Consequences

**Positive.** A doomed cell on a model-loading image stops costing its derivative
phase, twice. Redraws land sooner, so a promote with a flaky draw finishes
sooner. And a derivative result is never reported from a platform that failed
its own base contract.

**Accepted negative.** When base fails, that run tells you nothing about the
derivative layer. The redraw does. This trades per-run completeness for cost,
and is only defensible because the redraw exists.

**Accepted negative.** A chronically flaky base test would stop derivatives
being exercised at all, quietly. The guard is the suspect-host record and the
distinct-machine pattern from ADR 0029 — an image failing across many DIFFERENT
machines is the signal, and it still shows.

**Known gap.** The gate keys on the `base/` prefix, not on what a test means. A
cheap derivative test is blocked by a failed base just as an expensive one is,
and a hypothetically expensive base test is not covered at all. The prefix is
where the cost boundary actually sits today; if that stops being true, this rule
needs revisiting rather than extending.

## What would reverse this

- A promote where the derivative-phase result would have changed the decision
  and was not collected. That would mean the redraw is not doing its job, and
  the pair should be reconsidered together.
- Base becoming expensive — model downloads moving into provisioning are already
  sunk before test 12, but if `base/` itself grew a costly test the boundary
  would stop matching the cost.
