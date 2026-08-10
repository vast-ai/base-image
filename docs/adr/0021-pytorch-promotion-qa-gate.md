# ADR 0021 — QA-gated pytorch promotion, with mini in scope and a whole-run block

**Status:** Accepted
**Date:** 2026-08-07
**Extends:** [ADR 0005](0005-live-gpu-qa-gate.md) (live-GPU QA gate),
[ADR 0019](0019-base-image-promotion-qa-gate.md) (base promotion QA gate)
**Depends on:** [ADR 0020](0020-qa-verdicts-classify-by-fault-domain.md) — the hard
block below is only affordable because a bad host can no longer produce a `block`.

---

## Context

`promote-pytorch.yml` today has no QA reference at all: it resolves staging tags and
writes production tags, including the `-auto` pointers that Vast's
`@vastai-automatic-tag` backend serves to customers. ADR 0019 built exactly this
gate for base-image and it is live; pytorch is the same shape with a different
artifact set, so the question is what to copy and what to change.

### What actually ships (measured 2026-08-07 from `configs/pytorch.json`)

| family | entries | pointer surface |
|---|---|---|
| `configs` | 17 | 8 `cuda-*-auto` tags, mapped in `promote-pytorch.yml` |
| `mini` | 11 | none |
| `multi` | 1 | one floating alias, `pytorch:multi-210-291-271` |

The auto surface is far smaller than the artifact count suggests. The 8 auto tags
map onto **3 backends**, each resolving to the newest torch carrying it at
`default_python: 312`:

| backend | newest torch | auto tags pointing here |
|---|---|---|
| cu126 | 2.12.0 | `cuda-12.1.1`, `cuda-12.4.1`, `cuda-12.6.3` |
| cu128 | 2.11.0 | `cuda-12.8.1`, `cuda-12.9.2` |
| cu130 | 2.12.0 | `cuda-13.0.3`, `cuda-13.1.2`, `cuda-13.2.1` |

So **three images cover 100% of the automatic blast radius.** An early framing of
this work put it at "~100 artifacts, sampling required"; that was wrong by roughly
thirty-fold and would have spent the budget certifying artifacts no pointer points
at. It is recorded here because the error nearly drove the design.

### Why mini is different here than it was for base

ADR 0019 cond 5 declined mini QA for base-image on the reasoning that mini carries
no `-auto` tag, so a bad mini has no *automatic* path to a customer — it reaches one
only through a derivative build pinning an explicit dated tag, which is a reviewed
act. That reasoning was sound for base, where mini has two consumers.

It does not survive the pytorch numbers. Measured across
`derivatives/pytorch/derivatives/*/Dockerfile`:

- **15 derivative images pin a pytorch mini base. Zero pin a non-mini base.**
- Every pin is `cu128-cuda-12.9-mini`, at py310/311/312, torch 2.7.1 / 2.9.1 / 2.10.0.

Mini is not a side artifact in the pytorch family; it is the base layer for the
entire derivative estate. "No automatic path to a customer" is technically true and
practically irrelevant when the manual path is the one every derivative takes.

A second measured fact points the same way: mini is the **cheapest** cell in the
family (5.0–6.1 GB compressed, versus 8.4–12.2 GB for the auto configs and 14.8 GB
for multi). It is the best coverage-per-dollar in the matrix, not a luxury.

Note also that `multi` is built **on a mini base**, so a multi cell transitively
exercises the runtime-subset CUDA userland as well.

### Why multi is in scope where base's mini was not

`pytorch:multi-210-291-271` is a **floating, mutable alias** — the only non-`-auto`
mutable tag in the family. It moves on every promotion, untested. That is a pointer
by any honest reading, so ADR 0019 cond 5's own criterion puts it in scope.

---

## Options considered

### On what a failed cell does

**A — Advisory cells.** Report mini/multi results; promote regardless.
**Rejected.** A gate that cannot say no is decoration, and this repo has already
decided that once. It would also make the mini cells pure cost.

**B — Per-artifact hold.** Extend the flip/hold mechanism from auto tags to dated
tags, so a failed mini cell withholds only the mini tags.
**Rejected.** It adds conditional behaviour to the job that writes production tags —
the highest-risk place in the system — and it produces partially-promoted releases
where some artifacts of a build are public and others are not. That state is hard to
reason about later and hard to reverse.

**C — Any `block` stops the entire promotion.** **Chosen.**
Mechanically the *simplest* of the three: a `needs:` edge and a failed job, with no
new conditional logic inside the promote job at all. It is stricter than base-image,
which promotes dated tags regardless and holds only auto tags.

The objection to C is real and was raised before adoption: one flaky cell stops a
whole release, and with mini in scope there are more cells and therefore more draws
from a spot market per run. That objection is answered not by softening C but by
ADR 0020 — a host-attributable outcome can no longer produce a `block`. C is only
sound on top of that, which is why the dependency is declared rather than implied.

### On hardware

**D — Multi-GPU cells to exercise collectives.** **Rejected for now.**
The decisive measured fact is that the NCCL tests **already exist** and are
structurally unable to fail: `pytorch.d/10-torch-core.sh` runs a full `mp.spawn`
harness (`all_reduce`, `broadcast`, `all_gather`) gated on `device_count > 1`, and
falls through on a single GPU to a bare `echo` — not a skip — so the enclosing test
passes either way. `base/61-cuda-compute.sh` similarly turns a peer-access failure
into a `WARN` and then calls `test_pass`. Renting two GPUs today would produce a
green cell that certifies nothing.

Further, the image-owned half of that surface is reachable on **one** GPU:
`init_process_group('nccl', world_size=1)` exercises whether `libnccl` is present,
dlopen-able, ABI-matched to the wheel, and whether `ncclCommInitRank` succeeds —
which is what breaks when a torch/CUDA backend pairing is wrong. What genuinely
needs a second GPU is the *transport*, which is a property of the host, not the
image, and therefore fails in the fault domain ADR 0020 excludes from blocking.

**One GPU catches the defects we can fix; two catch the ones we cannot.**

**E — Single GPU. Chosen.** Multi-GPU is revisitable, but only after the two
existing tests are made capable of failing, and after `/dev/shm` on the offer tier
the selector actually picks has been measured.

---

## Decision

**1. `promote-pytorch.yml` gains the ADR 0019 gate topology**: `preflight` →
`resolve-digests` (pin every artifact's digest, publish run-scoped `qa-<run_id>-<key>`
aliases) → `qa` matrix → `qa-summary` → human approval (`environment: production`) →
`promote`. Every promoted artifact is copied **by digest**, never by a mutable
staging tag. There is no bypass input.

**2. The gated set is 78 cells, all single-GPU:**

| cells | what | python |
|---|---|---|
| 5 | one per config backend (cu126, cu128, cu129, cu130, cu132) | 312 |
| 72 | **every mini artifact the build produces** | every python built (310–314) |
| 1 | `multi` | 312 |

*(Restated 2026-08-10. The count moved twice: first from a wrong "3 backends /
56 cells" — the table builds cu129 too, which `AUTO_TAG_MAP` does not reference,
so it is promoted with no pointer and gated as ordinary coverage — and then
again when the table was brought up to date with upstream (torch 2.12.1 and
2.13.0 added, cu132 added as a new backend). Cell counts are DERIVED from the
config table and asserted by test; treat any number written here as a snapshot
and the test as the authority.)*

The auto cells are derived from the config table's backends, but the thing that
decides customer exposure is `AUTO_TAG_MAP`. Those agree today and nothing made
them agree, so a test asserts every backend an auto tag points at has a cell.
The reverse direction — a gated backend with no auto tag — is safe and allowed.

**Mini is gated exhaustively, not sampled.** Every artifact the build emits is
tested: 11 mini configs across 4–5 pythons each. The count is derived from the
config table, so it tracks the build automatically rather than being a list that
rots.

The reasoning that makes exhaustive coverage the right call here rather than
extravagant:

- **Mini is the base layer for the whole derivative estate.** 15 of 15 derivative
  Dockerfiles pin a pytorch mini base; zero pin a non-mini one.
- **Sampling has a specific hole that matters for this artifact.** A
  python-specific defect — a wheel built against the wrong ABI that still imports —
  is invisible to a default-python sweep, and mini exists in 5 python flavours.
- **Mini is the cheapest cell in the family** (5.0–6.1 GB compressed, versus
  8.4–12.2 GB for auto configs and 14.8 GB for multi), so exhaustive mini coverage
  is the cheapest coverage per artifact available.
- **Pytorch promotions are infrequent.** Per-promotion cost is the cheap axis;
  a bad base layer reaching 15 derivatives is the expensive one.

**`multi` stays gated for the same reason, on its own evidence.** It carries the
family's only non-`-auto` mutable alias (`pytorch:multi-210-291-271`, which moves
untested on every promotion today), and it is the base for the AIO studio image —
pinned at `derivatives/pytorch/derivatives/aio-studio/Dockerfile.base:1` and
`build-aio-studio-base.yml:18`. It is also built **on** a mini base, so its cell
exercises the runtime-subset CUDA userland transitively.

**3. Any `block` verdict on any cell stops the whole promotion.** No tags are
written — not auto, not dated. Per ADR 0020, `block` means the image is bad;
host-attributable outcomes are `inconclusive` and are retried.

**4. An exhausted `inconclusive` also stops the promotion**, with a reason line
stating the image was never tested rather than that it failed. Re-dispatching draws
fresh offers.

**5. Before any of the above is trusted, the tests must be able to fail.** The
required-pass gate matches on *test* states, so a conditional branch inside a
passing test is invisible to it. Three source fixes are prerequisites, not
follow-ups:
   - split the multi-GPU collectives block out of `10-torch-core.sh` into its own
     test file that `test_skip`s (exit 77) below 2 GPUs, so it can be *named* in
     `INSTANCE_TEST_REQUIRE_PASS` on a multi-GPU cell if one is ever added;
   - make a peer-access failure in `61-cuda-compute.sh` fail rather than warn;
   - add a `world_size=1` NCCL init assertion to every cell.

**6. `multi` asserts its venvs structurally.** A discovery-based test cannot detect
absence: a multi image shipping one of its three venvs passes green today. The
expected venv set is asserted explicitly. This costs nothing and needs no GPU.

**7. `templates/pytorch-qa/template.yml` carries the base-qa offer floors**
(`compute_cap gte 750`, `reliability2 gte 0.95`, `direct_port_count gte 64`,
`disk_space gte 16`, per-config `cuda_max_good` lower bound) plus a per-test timeout
sized for the venv count — the pytorch tests loop over every torch venv, and `multi`
has three, so base's single-venv timing does not transfer.

---

## Binding conditions

1. **The wiring is executed under test, not grepped.** The `wfexec.py` harness built
   for the base gate is reused: workflow steps are extracted and run under bash
   against a stubbed registry. String-matching a workflow file has produced false
   green in this repo three times.
2. **`block` cannot be bypassed or retried.** Asserted by test, as ADR 0020 cond 2.
3. **The QA label uses the reaper's scope prefix.** `reap_orphans.py` matches
   instance labels by `startswith`; a pytorch label outside that prefix would leave
   orphans unreaped while the reaper reported success. Asserted by test.
4. **GPU count is verified on the launched instance.** Disk is verified today and
   GPU count is not. Any cell that specifies a GPU count and does not get it must be
   treated as a bad box (`inconclusive`, retried), never as a pass.
5. **Cost and wall-clock are measured after the first three promotions.** Estimated
   at $25–30 per promotion (78 cells). If actual materially exceeds that, the mini
   set is cut — first to one cell per config at default python (11), then to the
   artifacts derivatives pin (7) — by ADR amendment, never by per-run exemption.
6. **Coverage is stated, not implied.** See below, and it is restated in the
   approval summary.
7. **The gated set is DERIVED from the config table, never hand-maintained.** A test
   asserts that the QA matrix covers **every** mini artifact the build produces —
   equality, not containment, so a new mini config or a new python cannot be added
   to the build without also entering the gate. A hand-written cell list is correct
   the day it is written and silently wrong at the next table edit; that is the
   exact drift the config-table extraction was done to remove. The test must fail
   loudly on an uncovered artifact rather than skipping.
8. **Matrix concurrency is bounded and the rate limit is respected.** 78 cells run
   against a single QA API key with no semaphore (ADR 0005 cond 6, still open). The
   client already backs off with jitter on 429/5xx, and ADR 0020 makes a genuine
   rate-limit casualty `inconclusive`-and-retried rather than a block — but
   `max-parallel` must be chosen deliberately and recorded, not left at a default,
   and 429 incidence is reviewed after the first runs.

---

## What a pytorch promotion does and does not certify

- **amd64 only.** arm64 children are promoted untested (ADR 0019 cond 5; parked on
  market size).
- **Mini: complete.** Every mini artifact the build produces is booted and tested,
  across every python built. No sampling.
- **Auto surface and `multi`: `default_python` (312) only** — which is the exact
  pointer surface those tags expose, so this is scope rather than a gap. The
  non-default-python `configs` artifacts promote untested via dated tags; they carry
  no pointer and, unlike mini, no derivative pins them.
- **Single GPU.** Collectives across devices are not exercised by any cell.
- **One box, one moment.** Not a guarantee under load or over time.
- **A pass after retries is still one box.** Retrying improves the odds of obtaining
  a verdict; it does not broaden coverage.

---

## Consequences

**Positive**

- The mutable pointer surface — 8 auto tags plus the multi alias — can no longer
  move to an untested digest.
- The base layer of the derivative estate is tested **exhaustively** before it is
  published — every mini artifact, every python — so a derivative can bump its pin
  to any promoted mini tag and know that exact artifact was booted.
- Python-specific defects in mini become detectable. A wheel built against the wrong
  ABI that still imports is invisible to a default-python sweep; it is not invisible
  to this one.
- Whole-run blocking removes partially-promoted releases as a possible state.
- Three source fixes convert two tests that could not fail into tests that can, which
  is worth more than the cells that run them.

**Accepted negative**

- One genuinely bad artifact stops the release for everything, including healthy
  artifacts. This is the deliberate trade: pytorch images are the base layer for the
  derivative estate, so shipping a known-bad one is worse than shipping nothing.
- ~78 instance rentals per promotion, plus retries — roughly $25–30 and a QA phase
  measured in hours rather than minutes. Accepted on the grounds that pytorch
  promotions are infrequent, so per-promotion cost and latency are the cheap axes
  and coverage of the derivative estate's base layer is the expensive one.
- The QA phase becomes the dominant cost and duration of a promotion, which makes
  `max-parallel` and the single-key rate limit operational concerns rather than
  incidental settings (cond 8).
- Stricter semantics than base-image, so the two gates differ. Recorded here
  deliberately; the difference is justified by mini's consumer count, not by taste.
- Non-default-python mini artifacts that derivatives actually pin remain untested.

---

## What would reverse this

- **Sustained whole-run blocks from single cells that turn out to be host artefacts.**
  That would mean ADR 0020's classification is not sound, and the block should
  narrow to per-artifact holds (option B) despite their cost.
- **Derivatives moving off mini bases.** The mini decision here rests entirely on
  15-of-15 consumption. If that ratio inverts, mini returns to base-image's
  treatment.
- **Cost materially above the estimate** with no defect ever caught by the mini
  cells — that would make the mini set unearned, and cond 5 is the pre-committed
  response.
- **A pointer appearing on a non-default python.** That would make the
  default-python-only scope indefensible rather than merely incomplete.
