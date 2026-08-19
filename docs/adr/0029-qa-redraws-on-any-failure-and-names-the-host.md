# ADR 0029 — A QA cell redraws on any failure, and names the machine it failed on

## Status

Accepted. **Supersedes the redraw clause of ADR 0019** (the "zero failures with a
non-zero exit" rule, and its 2026-08-14 extension to exit 5). Built and merged as
`fix/qa-redraw-and-suspect-hosts`.

**AMENDED 2026-08-19** — see *Amendment 2026-08-19 — the evidence was partly
misclassified*, near the end of this file. The decision stands and the redraw stays. But three of the six
exhibits below are misclassified, two of those three are the same machine, and
decision 3 has gained a precondition as a result. The original text is left
standing rather than rewritten: what was believed on the day is part of the
record. Read the amendment before acting on the evidence list.

## Date

2026-08-19

## Context

`qa-gate.yml` is the one live-GPU gate. `promote-base-image.yml`,
`promote-pytorch.yml`, `build-comfyui.yml` and `build-vllm.yml` all call it, and
none of them carries retry logic of its own. **Base-image is the source of truth
for how tests behave**, so a rule established here is the rule everywhere, and a
change here needs recording rather than discovering.

ADR 0019 established: a cell redraws when the run produced **zero failed tests**
with a non-zero exit. The reasoning was sound for the case that prompted it — a
rented host presenting no GPU, where `60/61/62` SKIP and the required-pass gate
turns those skips into a block. Nothing was tested, so another draw is the right
answer. A test that actually FAILED was treated as evidence about the image, and
never retried: *"retrying a real red until it passes by luck remains the thing
this gate must never do."*

That rule met its first large sample on 2026-08-18, when the pytorch gate ran 70
cells for the first time. **Six blocked. Every one investigated passed on other
hardware.**

- **NCCL segfault, machine 35974.** `ncclCuMemHostEnable` crashed inside
  `libcuda.so.1` during `ncclCudaLibraryInit`. Two controls on the *identical*
  driver build (570.133.07) — one of them the same Ampere generation — ran the
  same image digest clean. No NCCL environment variable avoided it; the crash is
  in the init-time probe.
- **Two multi-GPU collectives timeouts.** The same test then passed **10/10** on
  the same GPU model, driver build, torch version and image digest — standalone,
  and in real suite order after `40-nccl-init`.
- **Three supervisord boot races.** `supervisorctl cannot communicate with
  supervisord (exit 4)`, with `20-portal` and `26-caddy-auth` failing as
  collateral of that one root cause.
  **CORRECTED — see the Amendment.** These three cells have three different root
  causes, only one of which involves supervisord, and two of the three ran on the
  same machine. All three are defects in our own test suite, not in the host.

None was an image defect — true, and still true, but it hid a third possibility
the ADR did not have a name for: a defect in the TEST SUITE. See the Amendment.
All six produced **failed tests**, so the zero-failure
rule could not redraw any of them. That is the flaw: it was built for a host that
makes tests SKIP, and a bad host mostly makes tests FAIL.

An earlier attempt at this — the fault-domain model described in option C below,
drafted on the pytorch config-table branch and never merged — was dropped on
2026-08-18 during a rebase, to keep main's conventions while that branch landed. This ADR reaches the same conclusion from
the opposite direction, with the evidence the first run produced, and adds the
part that draft did not have: naming the host.

## Options considered

### A. Keep the zero-failure rule (rejected)

Cheapest, and it preserves the strong "never retry a red" property.

Rejected on measurement: it did not redraw a single one of the six cells it
should have. Under all-or-nothing promotion (ADR 0019) each of those blocked
every tag in the batch, so the rule's real cost was two full dispatch cycles for
faults that were not ours.

### B. Raise the QA offer floors so bad hosts are never drawn (rejected)

`set_filters` already carries `cuda_max_good`; a driver floor looked plausible
when the first failure appeared on driver 570.133.07.

Rejected because it is **positively contraindicated by the evidence**. The two
collectives failures were on driver 595.71.05, and the same combination passes
10/10 elsewhere; the segfault host's driver build passes on two other machines.
There is no filterable property that separates these hosts — the fault is the
individual machine, not any attribute we can express in a search query. A floor
would have excluded healthy capacity and still drawn the bad boxes.

### C. Fault-domain classification in `qa_verdict.py` (rejected for now)

Classify each outcome into a HOST or an IMAGE fault domain and retry only the
former: a timeout, a dead instance, `no_offers` and `bad_instance` are the host's;
a real assertion failure, a required-test miss and a config error are the image's.
Cleaner in principle, and it moves the decision into tested Python.

This was drafted as ADR 0020 on the pytorch config-table branch and **never
landed on `main`** — the number is unallocated in `docs/adr/` here, so the model
is summarised above rather than cited, and a reader should not go looking for it.

Rejected as the immediate step because it is a larger change to a shared verdict
path, and because the discriminator it needs — *does this reproduce?* — is
exactly what a redraw already measures. Reconsider if the redraw proves too coarse; the two are compatible.

### D. Redraw on any failure, and record the host (chosen)

Reproducibility becomes the discriminator rather than the symptom.

## Decision

**A cell redraws on any non-zero exit except `config_error` (4), and every failed
attempt names the machine it ran on.**

1. **Redraw on any failure.** `config_error` stays excluded: it is our own bug,
   and retrying it makes a broken gate look healthy. `bad_instance` (3) is no
   longer excluded — the client's `MAX_LAUNCH_ATTEMPTS` are about reaching a
   *running* box, which says nothing about whether that box can pass the suite.

2. **The machine id is captured at the moment of failure.** `test_template.py`
   carries `machine_id` out of the launch loop into `--raw`. The gate appends
   every failed attempt to `/tmp/qa-suspect-hosts.txt`, prints a `SUSPECT-HOST`
   line, and renders a job-summary table. Capture must happen there: the instance
   is destroyed immediately afterwards and the offer is gone.

3. **The report distinguishes exoneration from suspicion**, because the same list
   means opposite things:
   - *passed after redraw* → the image is exonerated by the redraw, so the fault
     is the host: a **de-verification candidate**.
   - *never passed* → the image is still a live suspect, and these hosts are
     **not** evidence. De-verifying them on this basis would be wrong.

   **AMENDED — a redraw pass is now necessary but not sufficient for the first
   branch.** See the Amendment: a passing redraw has three possible causes, not
   two, and as written this clause would nominate a healthy machine.

4. **A deterministic image defect still blocks.** It fails every draw, and the
   attempt count bounds the loop.

## Binding conditions

1. **The accepted cost is stated, not hidden.** A FLAKY image defect — failing on
   some boxes, passing on others — can now escape, because one passing redraw
   ends the loop. ADR 0019's warning was not wrong; it is being traded
   deliberately against six cells lost to bad hosts. What makes it survivable is
   the record: an image failing across many DIFFERENT machines is a pattern, not
   a green tick.
2. **The suspect record is what makes redrawing a red defensible.** If the
   machine capture is ever removed, this decision no longer holds and the
   zero-failure rule should come back. Guarded by
   `test_a_flaky_image_defect_can_now_pass_by_luck_AND_IS_RECORDED`.
3. **De-verification requires the exonerating pass.** A machine may only be
   de-verified on the strength of a later PASS of the same image. Without it the
   image is unexonerated and the host is not proven guilty.
4. **`config_error` is never retried.**
5. **Base-image is the reference.** Any future change to retry or verdict
   semantics lands in `qa-gate.yml` and is recorded here, not forked per image.

## Consequences

**Positive.** A bad draw costs one extra cell instead of a whole dispatch cycle.
Bad machines become actionable — previously the evidence was destroyed with the
instance. The gate stops reporting host faults as image faults, which is the
failure mode most likely to erode trust in it.

**Accepted negative.** A flaky image defect can pass by luck (condition 1). Cells
that fail take longer, since a failure now costs a redraw. Money: each redraw is
another rental.

**Accepted negative.** The de-verification signal needs a human. Nothing
automatically reports a machine to Vast; the gate produces the list.

**Known gap.** Suspects are reported per cell in that job's summary. There is no
aggregate across a whole promote, so a machine failing several cells appears
several times rather than once with a count. Cheap to add once there is real data
to shape it.

**Known gap.** No threshold on distinct machines. Failing across N different
hosts is the signal that an image is at fault, and nothing computes it yet — the
pattern is visible but not enforced.


## Amendment 2026-08-19 — the evidence was partly misclassified

A critical review of the merged change ranked the evidence base itself as a risk:
three of the six exhibits are supervisord boot races, in an artifact whose job is
to own supervisord ordering, which reads as a plausible flaky defect in the image
rather than in the host. That was worth settling, so the six cells were re-opened
against the raw job logs of the 2026-08-18 run, and the two mechanisms behind them
were measured directly in the shipped image.

The review's direction was right and its mechanism was wrong. Supervisord's
ordering is fine. The suite asks before the thing it is asking about exists.

### Corrected ledger

| cell | failed | actual root cause | class |
|---|---|---|---|
| `95768873001` | `40-nccl-init` | libcuda segfault, machine 35974 | host |
| `95780217275` | `41-nccl-collectives` (900s timeout) | collectives hang | host |
| `95786307193` | `41-nccl-collectives` (900s timeout) | collectives hang | host |
| `95775503365` | `10-supervisor`, `20-portal`, `26-caddy-auth` | `supervisorctl` raced the RPC socket | **harness** |
| `95783713144` | `20-portal`, `26-caddy-auth` | `10-supervisor` **passed**; the portal readiness budget was too small | **harness** |
| `95788610863` | `26-caddy-auth` | two auth-rejection checks exceeded curl's `--max-time 5` | **harness** |

Three corrections follow from it.

**Only one of the three involves supervisord.** In `95783713144` the supervisord
test passed outright; in `95788610863` the failure is `expected 401, got 000`,
which is a curl timeout, and its first attempt was separately a zero-failure
redraw of the kind ADR 0019 already handled.

**Two of the three are the same machine.** `95775503365` and `95783713144` ran on
one host — same address, same GPU model, same CPU model, same RAM to the
megabyte, same disk, consecutive port block. They were counted as two independent
exhibits. Six cells is really five hosts, and the largest bucket is one machine
twice plus an unrelated third.

**None of the three is a host fault.** In both portal cells,
`67-service-functionality` asserted the *same* endpoints — `/` and
`/get-applications` — and passed, 53 seconds later, on the same instance. The run
disproved its own failure without any redraw at all.

That inference carries the whole reclassification, and `67-service-functionality`
is a test that can pass *without asserting anything*: its portal block is guarded
by `if service_running instance_portal && wait_for_port 11111 5`, with an `else`
that prints a skip, and the file still ends in `test_pass`. A PASS is therefore
not evidence on its own — so here is the evidence. Both cells, from the raw job
logs:

    -- instance_portal --
    portal: serves HTML
    portal: /get-applications returns valid JSON

Those lines are inside the guarded block. They are only printed when it executed.
The portal was serving.

### The third category: a harness defect

ADR 0029 was written with two fault domains, host and image, and concluded "none
was an image defect." That is true, and it is what made the misclassification
possible: everything that was not an image defect fell to the host by default.

There is a third. A test can encode a timing assumption that a slower host
violates. The image is healthy, the host is healthy, and our own suite is what
failed. Measured in `vastai/base-image:cuda-13.2.0-auto`:

- `pgrep -f supervisord` is satisfied 1.7ms after `65-supervisor-launch.sh` forks
  it; `supervisorctl status` only becomes usable at 383ms. `10-supervisor` gated
  on the first and called the second on the next line. Reproduced locally on an
  idle 16-core box at 2 failures in 3 runs.
- `caddy hash-password` emits bcrypt at cost 14, and Caddy verifies an unknown
  username against a fake hash so timing cannot enumerate accounts. It caches
  successes only, so every distinct wrong credential — exactly what the
  auth-rejection checks send — pays a full verification: 690ms at 16 idle cores,
  4666ms at `--cpus=0.12`. `http_check` allowed 5s. Worse, the budget was
  self-amplifying: when curl gives up, Caddy carries on computing the abandoned
  hash on the same starved core the next check needs, which is why the production
  failure came in pairs.

Both are our assumptions, not the host's fault. A contended host only exposed
them — which is precisely what makes them look like host faults to a
reproducibility-based discriminator.

### What changes

**Decision 3 gains a precondition.** A passing redraw has three possible causes,
not two: a host fault, a flaky image defect (already acknowledged in binding
condition 1), and now a harness defect. Only the first justifies de-verifying a
machine.

> A machine becomes a **de-verification candidate** only when a redraw of the
> same image passed **and** nothing in the failing run itself contradicted the
> failure. A cell whose failing test was later contradicted by a passing test of
> the same property, in the same run, is **self-contradicted**: it is a harness
> defect, it produces no host suspicion, and it must not appear in the suspect
> list.
>
> **The counter-witness must have ASSERTED, not merely passed.** Nearly every
> service check in this suite is guarded by `if service_running X`, with a skip
> on the else branch and a `test_pass` at the end — so on a badly degraded
> instance the tests that prove least are the ones most likely to be green.
> Reading a bare PASS as a counter-witness would invert the rule: the more broken
> the host, the more exonerations it would manufacture. Cite the assertion's own
> output line, as above, or the cell is not self-contradicted.

This matters more than a taxonomy tidy-up. On the run that produced this ADR, the
unamended clause would have nominated one machine twice, on the strength of two
cells that our own suite disproved 53 seconds later. De-verification is an action
against someone else's hardware; being wrong about it costs a host operator
real money and costs us capacity we then cannot draw. A gate that manufactures
false accusations from its own timing bugs is worse than one that redraws
silently.

**Self-contradiction is also the cheapest signal available.** It needs no second
rental and no second host: the evidence is already inside the failing run. Where
it applies it is strictly better than a redraw, because it distinguishes a
harness defect from a host fault, and a redraw cannot.

### What was built

- **L069** (`check_no_presence_as_readiness_gate`) forbids asserting that a
  supervisord-managed process is up via `pgrep`/`pidof` unless something
  socket-backed appears earlier in the same file. It fired on three real sites,
  the baseline is clean again, and two mutation tests against a copy of the real
  tree pin it. Presence stays legal for identity and for asserting absence.
- **`lib.sh` carries the readiness**, so callers inherit it rather than each
  repeating it: `wait_for_supervisor` (bounded, memoised in the caller's shell),
  `assert_service_running` waiting for `RUNNING` with a `FATAL` short-circuit,
  and a socket guard on the predicates — an unreachable socket used to yield a
  word from an error message, so "cannot tell" was rendering as a definite answer.
- **The budgets are matched to the work they measure, and are now levers rather
  than baked constants**: `HTTP_CHECK_MAX_TIME` 5s to 20s, `PORTAL_READY_TIMEOUT`
  30s to 120s, and `CADDY_READY_TIMEOUT` 30s to 120s — that last one was a THIRD
  instance of the same defect, missed by the first pass: `wait_for_caddy` was
  still 30s against a measured 43s restart, and its expiry only WARNed, so the
  next check hit a port with no listener and recorded the same
  `expected 401, got 000` this ADR attributes to bcrypt. Every caller now fails
  the test on expiry. The suite ships inside the image, so a baked budget can
  only be corrected by rebuilding and re-promoting every image; behind a variable
  it is a template edit.
- **L070** floor-pins those defaults against the measurements. The first version
  of this amendment argued that budgets could not be gated because a linter
  cannot decide whether a number is large enough. It cannot decide sufficiency —
  but it can decide whether someone has put a budget back below what was
  measured, and until it did, reverting the two values that failed real cells
  passed every test in this repo.
- **A stub-`supervisorctl` harness** covers the `lib.sh` helpers with no GPU,
  container or supervisord. That file is sourced by every test in every image and
  had no automated coverage: `wait_for_supervisor` could be replaced by
  `return 0`, and accepting exit 1 as ready — the exact inversion of this fix —
  went undetected.

`docs/invariants.md` carries both, including the explicit note that the budgets
are FIXED but NOT GATED: a linter cannot decide whether a number is large enough,
so nothing stops the next one of these.

### Not built

**The self-contradiction detector.** Nothing computes "a later test asserted the
same property and passed" — the amended decision 3 is a rule a human applies when
reading the suspect list, not yet something the gate enforces. It needs two things
that do not exist: a mapping of which tests assert overlapping properties
(`20-portal` and `67-service-functionality` are the known pair), and a way to tell
an assertion from a skip in a suite where the skip is spelled `test_pass`.
Until both exist, **a suspect-list entry is a candidate for investigation, never
an instruction**, and the known harness pairs should be checked by hand first.

### Accepted cost: a dead instance now takes minutes to say so

Every one of these waits is time spent on a rented GPU to reach a conclusion the
old single-shot checks reached in ~0.1s. On an instance where supervisord never
serves, the degraded path is now roughly: `10-supervisor` 60s, `20-portal` 120s,
`25-caddy-proxy` 60+120s, `26-caddy-auth` and `27-caddy-tls` ~120s each (the
first caddy restart that does not come back fails the test, so it is one budget
rather than six), and 60s each in `65`, `67` and `75` — call it 11 minutes. The
memo does not span tests, because `runner.sh` runs each in its own bash process.

That is bounded and comfortably inside the 2400s cell timeout, and it buys the
thing the old behaviour got wrong: a fast answer that was often the wrong one.

The obvious optimisation — make `10-supervisor` `test_fatal`, aborting the suite,
since every downstream service assertion is meaningless without the socket —
would cut the degraded path to about a minute. It is deliberately NOT taken here.
A fatal abort changes what the run reports (no failed tests, so a redraw rather
than a block) and that is a verdict-semantics change, which per condition 5
belongs in its own decision rather than riding along with a harness fix.

### Rollout order, because the harness ships inside the image

These fixes only take effect in images built after they land, so the sequence is
part of the change rather than an afterthought:

1. **base** rebuild and promote — this is where `lib.sh` and `base/*.sh` live.
2. **pytorch** rebuild on the new base, and promote. Note that
   `promote-pytorch.yml` on `main` has no QA job: every gated pytorch promote so
   far has been dispatched from an unmerged branch. Until that lands on `main`,
   a pytorch promote either runs ungated or runs from a branch that must itself
   carry these fixes — otherwise the images bake the OLD harness and reproduce
   the same three failures.
3. **derivatives**, which is the part most easily missed. Nineteen of them pin a
   DATED base or pytorch tag and take `lib.sh` and `base/*.sh` from that layer,
   adding only their own `.d/` suite via `COPY ./ROOT /`. Until each pin is
   bumped and rebuilt, their gates keep the old budgets and the old presence
   checks. `external/vllm` and `external/sglang` are the exception — they copy
   `/ROOT` from the repo context, so they pick this up at their next build
   regardless of the pin.

**L069 and L070 are enforced by `imagegen lint --all`, which no workflow ran.**
Every gated invariant in this repo was checked on developer machines only. A
`lint-baseline` job now runs it, and also fails when `docs/lint-rules.md` is
stale, because a rule whose documentation has drifted is the one people read.

### What this does NOT change

The redraw stands. Three of the six cells were genuine host faults across three
machines, reproducibility remains the right discriminator for those, and option B
is still contraindicated for the reasons given above. What the amendment removes
is the assumption that everything a redraw clears was the host's fault. The
evidence base for the decision is half the size it claimed, and one of its two
arms argued for fixing the suite instead — which has now been done.

## What would reverse this

- A flaky image defect reaching production through a lucky redraw. That is the
  scenario ADR 0019 guarded against, and it would justify either the zero-failure
  rule returning or a distinct-machine threshold (the better answer).
- Redraws becoming common enough that QA cost or the single-key QA account (ADR
  0005 condition 6) becomes the binding constraint.
- Evidence that the failures were a property of the images after all — for
  instance the same machines passing other images at the same time. The
  investigation on 2026-08-18 found the opposite, but it was six cells — and the
  Amendment reduces that to three cells across three machines.
- A de-verification that turns out to have been wrong. If a machine reported by
  this gate is found healthy, the harness is the first suspect, not the host, and
  the self-contradiction detector stops being optional.
