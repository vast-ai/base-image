# ADR 0022 — PyTorch version lifecycle: what we keep building, and when we stop

**Status:** Accepted
**Date:** 2026-08-10
**Relates to:** [ADR 0021](0021-pytorch-promotion-qa-gate.md) (pytorch promotion
QA gate), [ADR 0019](0019-base-image-promotion-qa-gate.md)

---

## Context

Every pytorch promotion rebuilds **every** torch version in the table, back to
2.6.0. The practice is long-standing and the stated reason is good: it keeps
older torch versions shipping on a *current* base image, so a user on an old
torch still gets current OS packages, portal releases and CVE fixes.

Two things prompted a re-examination.

**The mechanism does not do what the intent describes.** A user pinned to
`2.6.0-cuda-12.6.3-py312-2026-06-15` gets nothing from a rebuild — the rebuild
mints `…-2026-08-10`, a *different, immutable* tag. Their pin is unchanged. The
benefit only reaches someone who **bumps**. That is a real use case (it is
exactly what happens when a derivative's pin is moved forward), but it means the
value is proportional to *bump activity*, not to how many people run old torch.
The practice keeps a landing pad current for moves that may never happen.

**ADR 0021 changed the risk.** Under the new gate, any blocked cell stops the
whole promotion. Rebuilding a 2.6-era stack on a 2026 platform is the single
most likely thing in the matrix to break — and there is direct precedent in this
repo, where a base bump to `setuptools>=81` removed `pkg_resources` and broke
dependencies that imported it, silently where the import sat behind a
`try/except`. Under ADR 0021 that same event would now stop torch 2.13.0 from
shipping. We would be coupling the release of the images people want to the
survival of the images nothing points at.

An attempt was made to settle this with evidence and **it failed to
discriminate**, which is recorded here so nobody repeats it: DockerHub exposes
`tag_last_pulled` but not per-tag pull counts, and all 812 dated tags showed a
last pull within ~24 hours across 966 distinct timestamps. That is equally
consistent with a mirror or scanner walking the repository daily and with broad
genuine use. It cannot separate the two, so it was not used as evidence.

Measured instead, from the repo itself: **85 of 177 images had neither an
`-auto` tag nor a derivative pin.**

---

## Options considered

### A — Keep rebuilding everything

**Rejected.** Not on cost — on risk concentration. It puts the oldest, most
fragile stacks on the blocking path for every release, in exchange for keeping a
landing pad current for versions nothing in the repo consumes. It also gets
worse over time: the tail only grows, so each new torch release makes the
matrix slower, dearer and likelier to be blocked by something unrelated to it.

### B — Keep the last N minor lines

**Rejected, and it is instructive why.** With any N small enough to help, this
retires **2.7 — which five derivative images pin** — while keeping 2.8, which
nothing uses. Recency is a proxy for relevance and it is the wrong proxy here.
This option is recorded because it is the obvious rule and it is actively
harmful.

### C — Tiered gating: rebuild everything, but let only "supported" versions block

Build the long tail but make its QA failures non-blocking, with a pre-committed
rule that repeated failure triggers retirement.

**Rejected.** It reintroduces a red check nobody acts on, which this repo has
already rejected twice (ADR 0019's `SKIP_QA` removal, and the advisory-cells
option in the mini/arm64 review). It also keeps paying to build and QA artifacts
whose failure has been declared not to matter, which is the worst of both.

### D — Support floor derived from consumption

**Chosen.** See below.

---

## Decision

The pytorch build matrix carries a torch version if **both** hold.

**1. It is the newest patch of its minor line.** A new patch SUPERSEDES the old
one in place; two patches of one minor never coexist. This is not new — the
table has always carried 2.7.1 and not 2.7.0, 2.9.1 and not 2.9.0. It was an
implicit convention, noticed only when adding 2.12.1 alongside 2.12.0 broke it,
and is now written down and enforced.

**2. Its minor line is at or above the SUPPORT FLOOR**, where the floor is
**the oldest torch minor that any derivative in this repo pins**.

The floor is *derived*, never a hand-set number. That is the whole point: it
tracks what is actually consumed, and it rises **on its own** as derivatives are
bumped forward, with nobody having to remember to raise it. Today the floor is
**2.7** (pinned by a1111, fluxgym, wan2gp, whisper, invokeai and others), so
2.6 retires and everything from 2.7 up is built.

**Retirement is not deletion.** Every dated tag already published stays pullable
forever. Retiring a version only stops new ones being minted, so nothing anyone
already runs can break. A retired version can be un-retired by adding it back
with a reason.

**3. A CUDA BACKEND is retired when nothing points at it.** The two rules above
govern torch *versions*; this covers the other axis. A backend earns its place by
being reachable — an `-auto` tag in `AUTO_TAG_MAP`, a mini variant, or a
derivative pin. A backend with none of those builds artifacts nobody can arrive
at except by typing a dated tag by hand.

*(Added 2026-08-12. The gap was found when cu129 failed QA on Blackwell hardware
and the obvious question — "what does cu129 actually serve?" — turned out to be
"nothing": no auto tag, no mini, no derivative pin. It had also just been brought
current from torch 2.8.0 to 2.13.0 on the reasoning that it should not be an
anomaly, which added 15 images nothing points at. Rule 3 is what stops that
happening again by inattention.)*

### Applied at adoption

- **torch 2.6 retired** — below the floor, no derivative pin, no `-auto` tag.
- **torch 2.12.0 retired** — superseded by 2.12.1 within the same minor.
- **backend cu129 retired (2026-08-12)** — no auto tag (`cuda-12.9.2-auto` has
  always pointed at cu128), no mini, no derivative pin. Its three torch versions
  (2.8.0, 2.12.1, 2.13.0) all remain on other backends, so nothing is lost.
  Removing it also removes the only backend whose upstream torchvision has no
  Blackwell kernels while its torch does — a split that would have needed a
  per-backend workaround to keep testing something nobody used.

Effect at adoption: 178 images to 151. With cu129 also retired, 144.

---

## Binding conditions

1. **A pinned version can never be retired.** Enforced by
   `test_no_pinned_version_is_retired`, which reads the derivative Dockerfiles
   and build workflows rather than a list. This is the direction that actually
   causes harm — retiring something still in use breaks a derivative build with
   no warning and no obvious cause — and it must never be weakened. The reverse
   error (keeping something too long) is merely wasteful.
2. **The floor is derived, and the derivation is guarded.** If the pin
   extraction stops matching, the floor collapses and every rule passes
   vacuously; `test_the_floor_is_derived_from_real_pins_not_a_constant` and
   `test_the_floor_is_actually_load_bearing` exist for that.
3. **Retirement is recorded, not silent.** The retired set is listed in the test
   file, so a retired version reappearing fails rather than slipping back in as
   a copy-paste of a neighbouring entry.
4. **Bump before you retire.** The order is: move the derivative pins forward,
   confirm the tests pass, then retire. Never the reverse.
5. **This governs what is BUILT, not what exists.** No published tag is ever
   deleted by this policy. Any proposal to actually delete tags is a different
   decision with a different blast radius and needs its own ADR.

---

## Consequences

**Positive**

- The oldest, most fragile stacks leave the blocking path, so an unrelated
  breakage in a 2.6-era build can no longer stop a current release.
- The matrix stops growing monotonically; each release retires roughly as much
  as it adds once the floor moves.
- "What do we support?" has a checkable answer derived from real consumption,
  rather than being whatever nobody got round to deleting.
- Catching up to upstream while pruning takes the matrix from 123 images (today
  on `main`) to 151, rather than to 178 — two new torch lines and a new CUDA
  backend for a net +28 instead of +55.

**Accepted negative**

- A user who wants an old torch on a current base loses that option once the
  version falls below the floor. Mitigated by retirement not being deletion, and
  by the floor being consumption-driven — a version only falls below it once
  nothing in the estate uses it.
- The floor is only as good as the pins it reads. A consumer outside this repo
  (a customer's Dockerfile, a Vast-side template) is invisible to it. This is
  the known gap; see below.
- Retiring 2.12.0 in the same change as adopting 2.12.1 means a version that
  built on `main` yesterday does not build tomorrow. Deliberate, and the reason
  is that they differ by a patch.

---

## What would reverse this

- **Evidence of real external consumption below the floor.** The floor reads
  only in-repo pins. If Vast has a support commitment to customers that old
  torch stays patched, that is a product commitment and it outranks this ADR
  entirely — the policy would become "the floor is the oldest supported
  version", set by that commitment rather than by pins.
- **Per-tag pull counts becoming available.** That would replace the structural
  argument with a measured one, and could justify a lower floor.
- **The gate becoming reliable enough that old builds no longer threaten a
  release** — e.g. if ADR 0021's whole-run block were ever narrowed to
  per-artifact holds, the risk argument weakens and rebuilding the tail becomes
  cheap insurance again.
