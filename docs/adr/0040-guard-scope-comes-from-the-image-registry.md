# ADR 0040 — A guard's scope comes from the image registry, not a per-check glob

- **Status:** Accepted (conditional — see Binding conditions). Build sequenced from
  condition 3.
- **Date:** 2026-09-11
- **Decision owner:** Rob Ballantyne
- **Process:** defect found during review of an external contribution → ground-truth
  measurement across every image → critical review of the proposed fix, which found that
  the first-draft fix reproduced the defect it was closing → this record.
- **Interacts with:** [ADR 0005](0005-live-gpu-qa-gate.md) (live-GPU QA gate),
  [ADR 0006](0006-inadvertent-exposure-gate.md) (advisory ramp),
  [ADR 0019](0019-base-image-promotion-qa-gate.md) (fail-not-skip / required-pass). Nothing in
  those is overridden; this ADR is about whether their guards can *see* the images they
  govern.

## Context

A new derivative image was submitted with an instance test that called `fail_later` twice
and then exited via a bare `test_pass`. `fail_later` only records; `report_failures` is
what converts a record into a non-zero exit. Both deferred failures — an unauthenticated
endpoint and a public bind — were therefore discarded, and the cell would have reported
PASSED with `FAIL:` printed in its own log.

That exact defect is already a codified invariant: *"A deferred failure is reported before
every non-failing exit — GATED (L062)"* ([docs/invariants.md](../invariants.md)). The rule
exists. It did not fire. `imagegen lint --all` was **clean** on the branch, and the
contributor had every reason to trust it.

L062 did not fire because its scope is built from a glob:

```python
roots += sorted((repo / "derivatives").glob("*/ROOT/opt/instance-tools/tests"))
```

which resolves to exactly two directories — `derivatives/llama-cpp` and
`derivatives/pytorch`. It has never reached any pytorch-nested image (aio-studio, comfyui,
oobabooga, unsloth-studio), any external image (sglang, vllm, vllm-omni), or aio-studio's
second overlay at `ROOT_BASE/`. The four nested images that comply do so by habit, not
because anything checks. The rule was decorative for roughly three quarters of the tree.

Measured, not assumed: widening the scan to all 12 real test roots covers 54 test files,
and **exactly one fires** — the new image's. The widening lands on a clean baseline.

Reviewing the same contribution surfaced two more guards with the same shape, which is what
makes this a class rather than a bug:

- `tools/template_manager/tests/test_promote_notification_truth.py` encodes the rule that a
  promoting job carrying `!cancelled()` must name the results that still have to block.
  `test_the_ramp_still_blocks_on_the_things_it_should` short-circuits with
  `if not _serverless_cell_names(jobs): continue`. The consequence is exact: the only two
  ungated promoters in the tree are the only two build workflows with no serverless cell.
  **The guard covers every workflow that does not need it.**
- `tools/template_manager/tests/test_required_test_names.py` sets `TEMPLATES = REPO /
  "templates"` — top level only — so no derivative QA template's
  `INSTANCE_TEST_REQUIRE_PASS` names have ever been validated against the tests that exist.

The common failure is not any one glob. It is that each check re-derives "which images do I
apply to?" independently, in an expression written when the tree had a different shape, and
a scope that silently narrows produces a *green* result. A guard that cannot see an image
is indistinguishable, in CI, from a guard that has approved it. That is the same
green-means-nothing failure class ADR 0019 addresses for skipped tests, one level up: there
the *test* self-skipped, here the *rule* self-skips.

`tools/imagegen/imagegen/discover.py` already provides the authoritative enumeration —
`discover(repo)` returns every image with its class (`base`, `derivative`,
`pytorch-nested`, `external`) and its overlay. Nothing forced the checks to use it.

## Options considered

**A. Fix the three globs in place.** Smallest diff; closes the three known holes today.
Rejected as the whole answer: it treats three symptoms of one cause and leaves the next
check free to invent a fourth scope expression. The first draft of this fix proposed
widening L062 to `derivatives/**/ROOT/opt/instance-tools/tests` — which *still* misses
`ROOT_BASE` and all of `external/`. A fix that reproduces the defect while claiming to
close it is the strongest argument against per-check globs that could be made.

**B. Enumerate from the registry; forbid ad-hoc scope expressions.** Every check that
applies per-image derives its subjects from `discover(repo)` and the image's declared
overlays, never from a hand-written glob. Chosen — see Decision. Cost: `discover()` must
grow to carry secondary overlays, because `Image.root` is hardcoded to `d / "ROOT"` and
aio-studio's `ROOT_BASE` is invisible to it. That is real work, and it is the same latent
defect one layer down.

**C. Add a meta-check: assert every image is covered by every applicable rule.** Attractive
— it enforces coverage rather than trusting it. Rejected *for now* as the primary
mechanism: "applicable" has no machine-readable definition today, and several rules have
legitimate, documented exemptions (see the `<APP>_ARGS` convention, explicitly *not*
statically gated by design). Encoding applicability badly would produce either noise or a
second false sense of coverage. Retained as a follow-on once (B) makes scope explicit
enough to assert over.

**D. Require a mutation test per rule and treat that as sufficient.** The repo already
requires mutation tests, and they are why several rules are trustworthy. Rejected as
sufficient here because a mutation test corrupts *a* real image — typically one the check
can already see. It proves the rule bites; it says nothing about which images it is aimed
at. L062 has a passing mutation test and was still blind to 21 of 28 images.

**E. Leave the promote-notification guard's serverless precondition alone and fix only the
one workflow.** Rejected. It is the option that keeps CI green today and is precisely how
the hole was created: the precondition looks like scoping and functions as an exemption.

## Decision

1. **Per-image checks enumerate from `discover(repo)`, not from globs.** L062's roots, and
   any future check with per-image scope, are built from the registry plus the image's
   declared overlays. Hand-written path globs for image scope are not to be added.
2. **`discover()` carries every overlay an image ships**, not just `ROOT/`. `Image` gains
   the set of overlay directories (today: `ROOT/`, and `ROOT_BASE/` for aio-studio) so a
   check can iterate them without knowing which images are special.
3. **The promote-notification ramp guard loses its serverless precondition.** The rule
   "a promoting job carrying `!cancelled()` must explicitly name the results that block"
   is universal; serverless has nothing to do with it.
4. **`test_required_test_names.py` enumerates derivative template directories**, so
   `INSTANCE_TEST_REQUIRE_PASS` names are validated against tests that exist for every
   image, not only the two top-level templates.
5. **Each of the above lands with a mutation test** that corrupts a real image *outside the
   old scope* and asserts the check fires — per the repo's bug → invariant protocol. A
   widening whose mutation test targets an already-covered image proves nothing.

## Binding conditions

1. **The baseline must stay clean, and this was measured, not assumed.** Widened L062
   across all 12 test roots / 54 files: exactly one file fires, the one in the
   contribution that surfaced this. If a future widening reddens an image that is not the
   subject of the change, stop — that is either a latent bug to be recorded separately or
   evidence the invariant's boundary is wrong. Do not relax the rule to restore green.
2. **Condition 3 will turn `build-unsloth-studio.yml` RED and that is the correct
   outcome.** It is the last advisory ramp in the tree; every other gated image carries
   `needs.qa.result == 'success'`. The guard has simply never been able to see it. This is
   not to be landed silently as a "test fix".
3. **Sequencing — condition 3 does not land before unsloth-studio is gated.** Order:
   (a) gate `build-unsloth-studio.yml` on its own merits, with the two-green-runs evidence
   its own comment asks for; (b) then remove the serverless precondition, so the guard goes
   from blind to satisfied rather than from blind to blocking. Reversing this order stops a
   shipping release train on a meta-test, which is how guards acquire a reputation that
   gets them weakened.
4. **L062's widening is sequenced ahead of, or together with, the contribution that
   surfaced it.** If the widening lands first, that image is red on a rule its author was
   told not to touch. Either land them together or land the one-line test fix first.
5. **The exemptions stay documented, not implicit.** Where a rule genuinely does not apply
   to a class of image, that is recorded in [docs/invariants.md](../invariants.md) as a
   boundary, in the shape the `<APP>_ARGS` convention already uses. A scope filter buried
   in a check is an undocumented exemption, which is the defect this ADR exists to close.

## Consequences

**Positive.** A rule's reach stops depending on the tree's shape when it was written; new
images are covered on the day they are added, which is exactly when nobody is looking.
Adding an image class (a second nesting level, another overlay) is one registry change
rather than an unknown number of glob edits. The three known holes close, with the false
green each produced.

**Accepted negative.** `discover()` becomes load-bearing for correctness, not just for
generation — a defect there now silences rules broadly rather than failing loudly, which
argues for its own tests. Several checks get slower, scanning ~4x the files. Unsloth-studio
loses its advisory ramp earlier than its own comment envisaged. And (C) is deferred, so
coverage is still a property we arrange rather than one we assert — the next check with a
novel scope is not yet prevented from inventing one, only discouraged.

**Not addressed here.** That a QA suite can be green while the product path is untested —
the gate speaks only to loopback, never through the proxy customers actually use. That is a
real gap of a different kind and wants its own record.

## What would reverse this

- If `discover()` proves unable to express a legitimate scope some check needs, and the
  honest fix is a targeted expression rather than a registry entry — then the rule is
  "globs are documented exemptions with a recorded reason", not "globs are forbidden".
- If widening reddens images broadly rather than the one measured file, the invariants
  themselves are wrong or their boundaries are mis-stated, and that is the thing to fix
  first.
- If a machine-readable applicability model turns out to be tractable, (C) supersedes this
  ADR's mechanism: asserting coverage beats arranging it, and this becomes the stepping
  stone rather than the destination.
