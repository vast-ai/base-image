# Red-team output — PR #220 (fix/test-template-stall-and-offer-scoring)

_Verbatim output of the red-team agent. Ground truth for /audit._

---

I've read the brief, both grounding docs, the full diff, and the real `poll_until_running`, `make_offer_sort_key`, `_download_cost`, `_base_offer_score`, `launch_with_retry`, and the `main()` call site (`/tmp/.../scratchpad/tt.py` mirrors `tools/template_manager/test_template.py` on `origin/fix/test-template-stall-and-offer-scoring`). No embedded instruction/appeal-to-approval in the artifact — the diff and test docstrings are technical only; nothing to report on the trust-boundary front.

---

## Steelman (the strongest case for the change)

Both parts are reasonable and one is arguably load-bearing for the ADR itself:

- **Stall detection plugs a real hole in ADR-0005's own runtime budget.** Condition 7 budgets "`POLL_TIMEOUT` 40m + ... + ≤12 launch attempts ≈ ~3h worst case" under a 4h job cap. But `launch_with_retry` (line 1063) can call `poll_until_running` up to `MAX_LAUNCH_ATTEMPTS=12` times, and pre-change a host stuck mid-pull sits the *full* 2400s each time → 12 × 40m = 8h, which blows straight through the `timeout-minutes: 240` backstop into a `cancelled` job (the ADR explicitly calls a killed job "worse than a clean fail"). Capping a stalled attempt at ~900s makes 12 × 15m = 3h, which is exactly the ADR's stated worst case. So the feature makes the ADR's budget arithmetic actually true.
- **The abandon path is correctly wired:** `(None, None)` → `launch_with_retry` destroys the instance, blacklists offer *and* machine (lines 1064-1069), moves on — no leak, reaper backstops.
- **The tie-break genuinely respects "smallest viable box."** It is element 5 of the 5-tuple, after `total_overshoot`, `per_gpu_overshoot`, `cap_overshoot`, `num_gpus` (line 884). Elements 1-2 are continuous `(actual-floor)/floor` floats, so a tie requires *identical* `gpu_total_ram`, `gpu_ram`, `compute_cap`, and `num_gpus` — i.e. effectively the same hardware class (many 8×H200 offers). Reordering *within* that set cannot move selection to a bigger/costlier box class. I tried to break the "it doesn't touch ADR-0005 policy" claim and it holds.
- **The unit math is right.** `inet_mbps/8000*3600` GB/hr is correct for decimal GB (1 Mbps = 0.000125 GB/s). GiB-vs-GB sloppiness is ~7%, irrelevant to a tie-break.
- **Bonus:** the ranking is actually *invariant* to `disk_gb`'s magnitude — `tie(o) = disk_gb · (dph_o/gb_per_hour_o + dl_cost_per_gb_o)`, and `disk_gb` is one scalar common to every offer, so it scales all keys equally and cancels out of the ordering. That means the "is `disk_gb` a good download-volume proxy?" worry the brief raises is **moot for the outcome** — `disk_gb` only selects *which branch* runs (`if disk_gb`), not the ranking. Nice property, even if unintended.

---

## Strongest objection

**The entire stall heuristic rests on one unverified property of Vast's `status_msg` — its update cadence during a pull — and there is a concrete class of hosts where the heuristic abandons perfectly healthy machines.**

Trace the reset logic (lines 555-558):
```
current = status_msg.strip() if status_msg else status
if current and current != last_status:
    last_status = current
    last_progress = time.time()
```
If a provider returns an **empty or coarse `status_msg`** during image pull, `current` collapses to `actual_status` (`"loading"`), which is *constant*. So `last_progress` is set once on the first `"loading"` tick and **never reset again**. After 900s, line 603 abandons the offer — regardless of whether the pull is advancing fine.

**Condition under which it bites:** an allowlist image whose pull/provisioning legitimately exceeds 900s on a modestly-provisioned but healthy host that doesn't emit sub-900s-granularity `status_msg`. This is not exotic — the repo's CUDA+pytorch+vLLM/comfyui images are tens of GB, and a 40 GB pull at a common ~200 Mbps host is ~27 min > 15 min. The diff's own claim — *"any progress resets the clock, so slow-but-advancing pulls still get the full window"* — is **only true if `status_msg` carries changing content (byte counters/layer transitions) at <900s intervals**, and nothing in the code or ADR establishes that it does.

Why this is more than cosmetic: if the false-positive **correlates across the market** for a given large image (same image, similar host tier), all 12 attempts can be abandoned → `EXIT_BAD_INSTANCE` (exit 3, "inconclusive"). On the **schedule path that gate runs on** (ADR "As shipped — cron gates too"), inconclusive **soft-passes → silent promotion** of an image that would actually have passed live QA. That is precisely the "inconclusive → mass silent promotion" failure mode ADR conditions 6 and 7 were written to prevent — the change quietly reopens it via a new axis.

**The tests beg this exact question rather than answer it:** `_FrozenAPI` hard-codes a single fixed `status_msg`, and `_prog` hard-codes ten *distinct* strings. Neither is evidence about what Vast actually emits during a real pull. The 900s constant is asserted "generous" with no measured pull-duration data for the real allowlist images — while ADR condition 10 insists every VRAM/`compute_cap` floor be "validated by a real passing run." The stall timeout gets no such validation gate.

---

## Other serious weaknesses (ranked)

1. **The mirror failure: a flapping `status_msg` makes the feature a no-op on exactly its target workload.** Docker pulls show *multiple concurrent layers*; if Vast's `status_msg` reflects that (alternating/rolling layer lines), `current != last_status` on nearly every tick → clock resets forever → a genuine hang that keeps flapping its status is *never* caught, so the feature silently fails to fire on the real "stuck mid image-pull" case it was built for. Combined with objection #1, the heuristic can't be simultaneously correct for both single-layer stalls and multi-layer pulls without knowing the field's semantics — and the semantics are undocumented here. **Trigger:** any pull whose status string changes ≥ once per 900s while genuinely hung. **Severity:** defeats the stated purpose while adding surface.

2. **The 900s stall silently rewrites ADR-0005's boot-phase budget without updating the governing doc.** Condition 7 states the boot bound as "`POLL_TIMEOUT` 40m (a box not `running` by then is bad)." Effective per-attempt boot is now `min(40m, 900s)`. ADR condition 11 says "each condition is merge-blocking, not prose" — a change to the boot-timing assumption should land as an ADR edit + a validation artifact, not an undocumented module constant. **Severity:** governance/doc-drift; low functional risk but exactly the "degrade silently into aspirations" pattern condition 11 forbids.

3. **The tie-break's headline justification is outside the gate's own operating envelope.** The motivating scenario and `test_disk_aware_prefers_fast_host_for_big_model` use 8×H200 at **$31-53/hr** pulling **750 GB**. But ADR condition 7's cost ceiling is a `--max-price` of ~$2.00, and the gate's smoke models are "deliberately tiny" (Rollout section). Under the actual comfyui gate, `--max-price` filters those offers out entirely and the models are ~GB, so the tie-break is optimizing a case the gate-as-scoped never runs. It's added complexity on the gating path justified by a manual/large-model use case. **Severity:** low harm, but the test asserting "legacy picks the slow host = the bug" overstates the real-world impact.

4. **Degenerate `inf` when no offer reports `inet_down`.** If the market slice returns offers without `inet_down` (or all 0), every `_download_cost` is `inf` (line 806), the tie-break collapses to `inf==inf` for the whole VRAM-equal group, and selection falls to Python's stable-sort order over raw API order — i.e. effectively arbitrary/non-reproducible across runs. **Trigger:** a provider slice that omits `inet_down`. **Severity:** low (still picks *a* valid box), but undermines the reproducibility the QA gate wants.

5. **Stall check can fire against an already-`running` instance waiting on port publication.** The stall test sits after the `running`-but-no-ports branch (lines 577-608). Once `running`, `last_progress` resets once, then `current=="running"` stops resetting; if port mapping takes >900s to appear, a live, paying, running instance is abandoned as "stalled." **Trigger:** slow port publication. **Severity:** rare, survivable (retry loop continues), but it means "stalled in 'loading'" can misfire on a running box.

---

## Hidden assumptions

- That Vast's `status_msg` is **populated and changes at <900s granularity during a healthy pull**, for every provider/image the gate targets. The whole Part-1 correctness rides on this and it is never verified.
- That "no `status_msg` change" ⇔ "no progress." False for coarse/empty-status providers and for large single layers.
- That a stalled offer is *cheaper* to abandon than to wait out — true only if the retry market has an equally-viable non-stalling host; on a thin amd64/VRAM-band market (the ADR repeatedly warns this market is thin) abandoning can mean exhausting attempts → inconclusive.
- That the gate always passes `--max-price` (argparse default is `None`, line 1123; the cost ceiling only exists if CI supplies it) — otherwise the tie-break can steer toward the more expensive fast host with no rate backstop.
- That `disk_gb` (= `recommended_disk_space or 40`, line 1201) is a download proxy — true-ish but irrelevant, since it cancels out of the ranking.

---

## What would change my mind

- **Evidence of `status_msg` cadence from a real pull**: a captured trace showing Vast emits a *changing* `status_msg` at well under 900s throughout a multi-GB pull on the low-bandwidth host tier — for images with empty/coarse status too. That defuses the strongest objection directly.
- A **measured worst-case pull+provision duration** for the actual allowlist images on the low end of the VRAM band, showing 900s (or the chosen constant) sits comfortably above it with margin — the analogue of condition-10 floor validation.
- Changing the trigger from "status string unchanged" to something that can't false-fire on a healthy slow pull: e.g. require an *empty/absent* `status_msg` to be treated as "no signal" (fall back to full `POLL_TIMEOUT`), or key the stall on observed byte/layer-progress stagnation rather than raw string equality, or floor it on wall-clock-since-launch only after a confirmed no-progress signal.
- For the tie-break: confirmation the gate always supplies `--max-price`, plus a tie-break on `inf` that falls back to a deterministic key so an all-missing-`inet_down` slice stays reproducible.

Net: real upside (it makes ADR-0005's runtime budget actually hold, and the tie-break provably respects the smallest-box policy), but Part 1 ships an unvalidated heuristic with a concrete false-positive path into the ADR's own silent-promote failure mode. Every defect is fixable without redesign, and blast radius is a CI QA tool with a human-in-loop on the dispatch path.

VERDICT: serious-but-fixable
