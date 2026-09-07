# ADR 0038 — serverless mode is declared by the platform; the inference bridge is removed

- Status: accepted
- Date: 2026-09-07
- **Supersedes ADR 0034** (serverless mode is detected from the platform, not only declared)
- Related: ADR 0025 (retraction precedent), L077 (declared expiry), L079, L080

## Context

ADR 0034 shipped a deliberate BRIDGE. The autoscaler injected `MASTER_TOKEN` into every
worker but the platform did not inject `SERVERLESS`, so `01-detect-serverless.sh` inferred
the mode from `MASTER_TOKEN` + `REPORT_ADDR` when `SERVERLESS` was unset. It carried
`EXPIRES: 2026-11-25` and said, in its own words, that when the backend started injecting
the variable the file is DELETED, not kept.

The backend now inserts `SERVERLESS=true` when a worker is launched from the serverless
system. The bridge's own retirement condition is met.

## Decision

Delete the inference. `SERVERLESS` is read, never derived — in the image, and in the
`test_template.py` client that mirrored the image's inference so the two could not drift.

**The inference is the only thing removed.** Two things the stage owned outlive it:

1. **The cold-start block.** `update_portal=false` / `update_vast_cli=false` under
   serverless is not part of the bridge — it is a real optimisation on the workload where
   cold start is the product, and `boot_default.sh` defaults both to `true` with no other
   serverless-conditional anywhere. Deleting the file naively would have restored two
   network fetches to every serverless cold start, silently. It moves to `25-first-boot.sh`,
   beside the `first_boot/` scripts that read those flags.

   That placement is strictly better than stage 01, not merely equivalent: stage 10 has
   sourced `/etc/environment` by then, so a `SERVERLESS` a user set by hand in that file is
   honoured — which it never was under stage 01, running before it.

2. **The QA coverage.** Five cells reached serverless mode THROUGH the inference, carrying
   `MASTER_TOKEN` and `REPORT_ADDR` sentinels and no `SERVERLESS`. Removing the inference
   without touching them would have dropped them out of serverless mode while still
   requiring serverless tests to pass. They now DECLARE `SERVERLESS=true`, which is what a
   real worker receives. `promote-base-image`'s `qa-detect` matrix is kept rather than
   deleted with its premise: it is the only cell in which base's serverless path — base/85
   and pyworker on an image with no inference engine — runs at all.

## The retraction, and why one is not shipped

ADR 0034 made a retraction a **binding condition** of deletion: `10-prep-env.sh` dumps the
whole environment into `/etc/environment` on first boot, so a detected instance has
`SERVERLESS=true` baked into a file the deletion cannot reach. That reasoning is correct.
The conclusion no longer follows, and departing from a recorded condition is stated here
rather than left implicit.

Working the cases through, removal introduces no new breakage:

| instance | after removal |
|---|---|
| detected correctly | backend injects `SERVERLESS=true` anyway — unchanged |
| false positive | already darkened today — unchanged |

The retraction would REMEDIATE existing false positives; it is not a precondition for
removing the bridge. And it cannot be made precise: the verdict marker lived in `/run`,
which is tmpfs, so nothing distinguishes "we inferred it" from "the user typed it", and
`/etc/environment` is the user's file under ADR 0034's own ownership boundary. A strip
would be a guess with a real chance of clobbering a deliberate edit.

The exposure is also bounded: the bridge merged 2026-08-26 and reached promoted bases on
2026-09-01, so the affected population is instances created in that window which were
DETECTED rather than DECLARED — and 9 of 10 published autoscaler templates declare
`SERVERLESS` themselves.

Left as environment, which is what it now is. If a specific instance is found wrong, the
fix is an edit to `/etc/environment` on that instance, not a migration shipped to every image.

## Options considered

1. **Delete the inference; relocate what outlives it** — chosen.
2. **Delete the whole file** — rejected: takes the cold-start optimisation with it, as a
   silent latency regression on the one workload that cannot afford it.
3. **Keep the bridge until its declared expiry** — rejected. It is dead code the day the
   backend injects the variable, and a dormant inference that can still fire is exactly the
   thing worth removing early: its false positive darkens every interactive service on a
   customer's instance, permanently.
4. **Delete plus a strip migration** — rejected as a bundle. It ships a new bridge to remove
   an old one, cannot distinguish our value from the user's, and remediates a pre-existing
   condition rather than anything the removal causes. Available separately if a real
   instance is found wrong.
5. **Keep the client-side inference in `test_template.py`** — rejected: the client would
   believe a cell runs serverless while the instance does not, drift in the exact direction
   that helper exists to prevent.

## Consequences

- `VAST_SERVERLESS_DETECT`, the `/run/vast-serverless-detect` marker, `SERVERLESS_DETECT_EXPECT`
  and the `base/15-boot-markers` round-trip assertion are all gone with the mechanism.
- The per-image canary the inferred cells doubled as — "did this image actually inherit the
  stage" — goes with the bridge. L041 gates stale base pins statically, which is where that
  check belongs.
- One fewer `EXPIRES:` file for L077 to watch.

## What would reverse this

The backend ceasing to inject `SERVERLESS`. That would be a platform regression rather than
a design change, and the response is to fix the injection, not to re-infer from a proxy.
