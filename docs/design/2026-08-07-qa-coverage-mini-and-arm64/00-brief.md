# Design brief — QA coverage for mini images and arm64

**Status:** open for review. Nothing is being built against this yet.
**Date:** 2026-08-07
**Extends:** ADR 0019 (base promotion QA gate), ADR 0005 (live-GPU QA gate)

The gate as shipped tests **10 artifacts out of ~82 promoted**: cuda configs only,
default-python only, amd64 only. ADR 0019 cond 5 states this honestly and the
approval summary repeats it, but stating a gap is not closing it. Two specific
gaps are worth closing, and they are different problems with different answers.

---

## 1. Ground truth (measured 2026-08-07, not assumed)

### Mini images

| fact | evidence |
|---|---|
| Built **FROM** the full stock base, adding only CUDA runtime packages | `Dockerfile.runtime` — `FROM ${BASE_IMAGE}` where BASE is `stock-ubuntu24.04-py312` |
| Therefore **inherit `/opt/instance-tools` and can run the harness** | nothing in `Dockerfile.runtime` removes it |
| Are **consumed by real derivatives** | `derivatives/llama-cpp/Dockerfile`, `build-llama-cpp.yml`, `build-linux-desktop.yml` pin `cuda-12.9-mini-py312` (5 refs) and `cuda-13.2-mini-py312` (2 refs) |
| Carry **no `-auto` tag** | `configs/base-image.json` — mini entries have no cuda auto version |
| Are promoted **untested**, 10 tags per run | 2 mini × 5 pythons |

So: testing them is not blocked by anything technical. The image can run the suite
today. What is missing is a decision about what a mini failure should *do*.

### arm64 availability

Live offer counts, single GPU, 8 GB+ VRAM, reliability ≥ 0.95, 64 GB disk:

| query | offers | median $/hr | GPUs |
|---|---|---|---|
| amd64, full base-qa floors | **744** | 0.20 | RTX 5090 ×82, 4090 ×79, 3090 ×60 |
| arm64, full base-qa floors | **10** | 0.39 | GB10 ×10 |
| arm64, without the port floor | 10 | 0.43 | GB10 |
| arm64, rentable at all | 12 | 0.39 | GB10 |

Three things follow, and they shape the design more than the idea does:

1. **arm64 is available, but barely** — ~1.3% of the amd64 market. Non-zero, so
   "test if available" is viable; thin enough that "not available" will be a
   *routine* outcome, not an exception.
2. **The entire arm64 market is one GPU model (GB10).** An arm64 pass certifies
   Grace Blackwell and says nothing about any other arm64 host. Whatever we build,
   the coverage claim must say that.
3. **The `direct_port_count` floor costs nothing on arm64** (10 offers either way),
   so the new floor is not what makes arm64 scarce.

---

## 2. The challenge — "test-if-available" is the skip-as-pass hole, one level up

This is the objection to state before designing anything, because the naive shape
is very appealing and quietly wrong.

The whole fail-not-skip effort — `INSTANCE_TEST_REQUIRE_PASS`, `require_key`,
`require_tests`, and the removal of `SKIP_QA` — exists because **"the test did not
run" was indistinguishable from "the test passed"**. A rule of the form

> test arm64 if an offer exists, otherwise proceed

reintroduces exactly that at fleet level. If the arm64 market is empty for a month,
arm64 ships untested for a month and **every run looks green**. The failure is
silent, it is not attributable to any single run, and nothing accumulates. That is
strictly worse than today, where the gap is at least stated in the summary, because
it would look like coverage.

So the design constraint is: **absence of a test must produce a durable, visible,
decaying state — never a pass.**

There is a second, structural difficulty specific to arm64. The `-auto` tag points
at a **multi-arch index**. There is no way to flip "the amd64 half". So the two
obvious rules are both wrong:

- *Hold the index if arm64 is untestable* → a scarce arm64 market blocks amd64
  promotions entirely. Unacceptable; this is how a gate gets routed around.
- *Flip and say nothing* → the silent-rot case above.

The honest shape is a third thing: flip, and carry **coverage debt** that is
visible at the next approval and that escalates if it grows.

---

## 3. Options

### Mini images

**M1 — Advisory cells.** Add mini cells to the QA matrix; report results in the
summary; promote regardless.
*For:* trivial, no new promote semantics. *Against:* a red mini cell that changes
nothing is decoration, and this repo has already decided once that a gate which
cannot say no is not a gate.

**M2 — Per-artifact hold.** Extend flip/hold from auto tags to *dated tags*: a
failed mini cell means those 10 mini tags are not copied to prod, while everything
else promotes.
*For:* mirrors the existing, proven semantics; a real consequence; blast radius is
exactly the artifacts that failed; llama-cpp and linux-desktop keep pinning the
last good mini tag. *Against:* promote currently copies dated tags unconditionally,
so this is new machinery in the most safety-critical job.

**M3 — Block the promotion on a mini failure.** *For:* simple. *Against:*
disproportionate — one mini variant failing would hold ten healthy auto tags.

**Required-test set is its own question.** Mini is a CUDA *runtime* subset — it has
`cuda-nvcc`/`nvrtc` but not the full toolkit or math dev headers (cf. the known
`-mini nvcc off-PATH` behaviour). The amd64 GPU trio may not all apply, so mini
needs its own `require_tests`, chosen from what a mini image actually promises.

### arm64

**A1 — Opportunistic cell with coverage debt.** Add one arm64 cell per promotion.
`no_offers` → `inconclusive` (already the client's behaviour), which does **not**
hold the index but **does** record "arm64 not verified this run". The manifest
carries `arm64_last_verified` (date + digest); the approval summary shows it and
how many promotions have passed since; beyond a threshold it becomes a loud,
named condition rather than a footnote.
*For:* honest about a thin market; costs one cell; escalates instead of rotting;
uses the existing three-valued vocabulary. *Against:* new state that must persist
between runs (where? the registry, a repo file, or the last successful run's
artifact — artifacts expire in 7 days); the threshold is a judgement call.

**A2 — Test arm64, hold the index if it fails, ignore if unavailable.** *For:*
simplest rule that still has teeth on a real failure. *Against:* the "ignore if
unavailable" half is precisely the silent-rot case, with no accumulating signal.

**A3 — Separate arm64 gate on its own cadence** (e.g. a weekly scheduled run that
only verifies arm64 and reports), decoupled from promotion. *For:* does not couple
a scarce market to the promotion path at all; a scheduled run may legitimately
soft-pass under ADR 0005. *Against:* verifies a digest that may no longer be what
the auto tag serves, so it is monitoring rather than gating.

---

## 4. What I would need to decide

1. **For mini: what does a failure DO?** If the answer is "nothing", M1 is honest
   but the cells are decoration. My view is that M2 is the only option that matches
   what this repo has already decided about gates that cannot say no.
2. **For arm64: where does coverage debt live, and what threshold escalates it?**
   The state must outlive a 7-day artifact retention, which points at a committed
   file or a registry annotation rather than a run artifact.
3. **Is one GB10 host acceptable as "arm64 verified"?** It is the whole market, so
   the practical answer is yes — but the coverage statement must say GB10, not
   "arm64", or we repeat the overstatement the panel already caught once.

## 5. Recommendation going in

**M2 + A1.** Mini failures hold the mini dated tags (nothing else), and arm64 is an
opportunistic cell whose absence accumulates visible, escalating debt rather than
silently reading as coverage.

I would argue hardest against **A2's "ignore if unavailable"** in any form that does
not accumulate. That is the same defect as `SKIP_QA`, which was removed from this
gate today for the same reason: the untested-ness attached to the run instead of to
the artifact, so it disappeared.

## 6. Not yet done

First stage only. Per the repo's process this wants independent competing designs
and a critical review before an ADR, particularly for M2 — it adds conditional
behaviour to the job that writes production tags, which is the highest-risk place
in this system to add anything.
