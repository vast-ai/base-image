# Design brief — how a staging artifact is identified

**Status:** open for review. Nothing is being built against this yet.
**Date:** 2026-08-07
**Trigger:** a question during the first live CI run of the base promotion gate —
"surely a rebuild on the same day could get the wrong bits promoted anyway? The
better solution might be a more exact date tag on the staging images."

This brief exists because the answer is "partly, and the fix for the *correctness*
half is already in — but the identity question underneath it is real, is repo-wide,
and is worth settling deliberately rather than per-workflow."

---

## 1. The problem, stated precisely

A staging tag is `<repo>:<template>-py<ver>-<YYYY-MM-DD>`. The date is the *build*
date, and the tag is **mutable**: a second build on the same day rewrites it in
place. Three distinct problems follow, and they are worth separating because they
have different fixes and different urgencies.

**P1 — wrong bits promoted.** A rebuild between planning and writing changes what a
tag ref resolves to, so a pipeline that reads tags at write time can certify one
set of bits and ship another.

> **Already fixed** (commit `9d50209`). `resolve-digests` now pins every artifact's
> digest at plan time and `promote` copies all of them by digest, so the mutable tag
> is read exactly once. A missing pin fails the step rather than falling back to the
> tag. Mutation-verified. **P1 is not a reason to change the tag scheme.**

**P2 — ambiguous provenance.** "Promote 2026-08-06" does not name a build. If the
day had two builds, the operator cannot say which one they are promoting, the run
record does not say which one was promoted, and a promotion cannot be reproduced
later. Digest pinning makes the *outcome* deterministic; it does not make the
*intent* expressible.

**P3 — no way to re-promote a known build.** Because a rebuild destroys the
previous day's artifact tags, "promote yesterday's build, the one that was green"
is unsayable once a rebuild has happened.

P2 and P3 are what a more precise identifier would address. They are real but they
are **not** correctness bugs — they are auditability and operability gaps.

---

## 2. Constraints, verified against the repo

These are measured, not assumed. They bound the option space more than the idea
itself does.

| constraint | evidence | consequence |
|---|---|---|
| The dated-tag convention is repo-wide, not base-image-specific | 14 workflows write a `-$DATE` staging tag; 27 touch the staging namespace | A change here is a **convention change** across the image family, not a local edit |
| Three promote workflows share the `STAGING_DATE` input shape | `promote-base-image`, `promote-pytorch`, `promote-linux-desktop` | Changing one and not the others creates exactly the drift the config-table extraction removed |
| Prod dated tags are pinned by downstream images | 16 build workflows + 1 derivative Dockerfile pin e.g. `base-image:cuda-12.9-mini-py312-2026-06-15` | **The PROD tag format cannot change** without a coordinated bump of every derivative. Any option must confine itself to STAGING |
| The date is human-legible and is the operator's whole interface today | `STAGING_DATE: "2026-08-06"` | Replacing it with an opaque id is a real usability cost, paid every promotion |
| Registry manifests are content-addressed and survive tag rewrites | verified in the promote harness | An immutable *alias* is cheap; it does not require rebuilding or re-pushing anything |

---

## 3. The strongest objection to changing anything

Digest pinning already guarantees that what was tested is what ships. A build
identifier does not make any *artifact* safer — it makes the *conversation* about
artifacts more precise. That is worth something, but it is worth less than it feels
like during an incident, and it is paid for on every routine promotion by an
operator who now has to look up an id instead of typing a date.

There is also a specific failure mode to respect: this convention is shared by 14
workflows. A change adopted in `promote-base-image` alone produces two conventions
in one repo, which is strictly worse than either convention consistently applied.
The config-table extraction earlier in this work existed precisely to kill that
class of drift; reintroducing it here would be a regression in kind.

**So the burden of proof is on changing it, and the bar is: does this solve P2/P3
for the whole family, or is it a local convenience?**

---

## 4. Options

Presented in the order they were written; the labels are mechanical.

### A — Do nothing further

P1 is fixed. Accept that a promotion identifies a *day*, not a *build*, and that
the run record plus the pinned manifest artifact already capture which digests were
promoted after the fact.

- **For:** zero change to a 14-workflow convention; zero operator cost; the
  manifest artifact already answers "what did we actually ship" forensically.
- **Against:** does not answer "which build am I promoting" *before* approving, and
  the manifest artifact expires (7-day retention). P3 stays unsolved.

### B — Immutable per-build alias alongside the dated tag

Builds keep writing `-<date>` exactly as now, and additionally push
`-<date>.<build_run_id>`, which is never rewritten. Promote keeps accepting a date,
resolves it to the newest build alias, and **records that alias** in the manifest,
the approval summary and Slack.

- **For:** no interface change (date still works); prod tags untouched; P2 solved
  for reading (you can always see which build was promoted); P3 solved (an explicit
  alias can be promoted directly); it composes with digest pinning rather than
  replacing it; adoptable per-workflow without splitting the convention, because the
  dated tag keeps its current meaning everywhere.
- **Against:** more staging tags (storage, and a longer `crane ls`); a second naming
  concept to learn; "newest build alias" needs a tie-break rule.

### C — Replace the staging date with a build identifier

Staging tags become `-<build_run_id>` (or a timestamp). Promote takes that id.
Prod tags stay dated.

- **For:** unambiguous by construction; no mutable staging tag exists at all, so P1
  could not have happened even without pinning; smallest conceptual surface once
  adopted.
- **Against:** breaking interface change across three promote workflows and 14 build
  workflows; the operator loses the human-legible handle; staging becomes harder to
  browse; must be adopted everywhere at once or the repo carries two conventions.

### D — Timestamp precision on the existing date

`-YYYY-MM-DDTHHMM` instead of `-YYYY-MM-DD`.

- **For:** keeps one concept and stays sort-friendly and human-legible.
- **Against:** same breaking-change cost as C with less benefit — it is still not a
  build *identity*, just a finer-grained collision window, and two builds started in
  the same minute still collide. Strictly dominated by B or C.

---

## 5. What I would need to decide

1. **Is the target P2/P3, or is it "make P1 impossible by construction"?** If the
   latter, note that P1 is already closed by pinning, and option C's advantage is
   defence-in-depth rather than a fix.
2. **Family-wide or base-image-only?** A base-image-only change is the outcome I
   would argue hardest against.
3. **Is the operator cost of an opaque identifier acceptable?** This is the crux
   between B and C.

## 6. Recommendation going in

**B**, and only if we commit to rolling it across the family. It solves the real
gaps (P2, P3) at no interface cost, it is additive so it can land incrementally
without splitting the convention, and it leaves the prod tag format — the one thing
that is genuinely load-bearing for derivatives — untouched.

I would argue against C and D: both are breaking changes to a 14-workflow
convention, bought mainly for a correctness property that is already held by digest
pinning.

## 7. Not yet done

This brief is the first stage. Per the repo's own process, before anything is built
this wants independent competing designs and a critical review, and then an ADR
recording the decision and the rejected alternatives. Nothing here is settled.
