# ADR 0029 — A QA cell redraws on any failure, and names the machine it failed on

## Status

Accepted. **Supersedes the redraw clause of ADR 0019** (the "zero failures with a
non-zero exit" rule, and its 2026-08-14 extension to exit 5). Built and merged as
`fix/qa-redraw-and-suspect-hosts`.

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

None was an image defect. All six produced **failed tests**, so the zero-failure
rule could not redraw any of them. That is the flaw: it was built for a host that
makes tests SKIP, and a bad host mostly makes tests FAIL.

An earlier attempt at this — the fault-domain model of ADR 0020, on the pytorch
config-table branch — was dropped on 2026-08-18 during a rebase, to keep main's
conventions while that branch landed. This ADR reaches the same conclusion from
the opposite direction, with the evidence the first run produced, and adds the
part ADR 0020 did not have: naming the host.

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

ADR 0020's model: classify each outcome into HOST or IMAGE, retry the former.
Cleaner in principle, and it moves the decision into tested Python.

Rejected as the immediate step because it is a larger change to a shared verdict
path, and because the discriminator it needs — *does this reproduce?* — is
exactly what a redraw already measures. Reconsider if the redraw proves too
coarse; the two are compatible.

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

## What would reverse this

- A flaky image defect reaching production through a lucky redraw. That is the
  scenario ADR 0019 guarded against, and it would justify either the zero-failure
  rule returning or a distinct-machine threshold (the better answer).
- Redraws becoming common enough that QA cost or the single-key QA account (ADR
  0005 condition 6) becomes the binding constraint.
- Evidence that the failures were a property of the images after all — for
  instance the same machines passing other images at the same time. The
  investigation on 2026-08-18 found the opposite, but it was six cells.
