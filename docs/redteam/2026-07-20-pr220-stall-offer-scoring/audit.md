# Audit of orchestrator synthesis — PR #220 red-team

Independent auditor (red-team agent) read red-team.md, _brief.md, _my-synthesis.md,
and both shipped diffs. Verdict: **minor-spin**. Verbatim below.

---

## Audit: orchestrator synthesis vs. red-team ground truth

I read `red-team.md`, `_brief.md`, `_my-synthesis.md`, and both shipped diffs (base-image `361da77`, vast_landing `d3a15335`), and confirmed the branch tip `fix/test-template-stall-and-offer-scoring` is `361da77` with the guard live at line 620.

### The core distortion: the strongest objection is relabeled and its unfixed half is called a "framing nit"

The red-team's **strongest objection** (red-team.md lines 25–40) is NOT "empty status_msg." Its thesis is broader: the heuristic assumes `status_msg` changes at sub-900s granularity, and it does not, in **two** ways. Line 36 spells out the concrete, non-exotic case explicitly: "an allowlist image whose pull... legitimately exceeds 900s on a modestly-provisioned but healthy host that doesn't emit sub-900s-granularity `status_msg`... a 40 GB pull at a common ~200 Mbps host is ~27 min > 15 min." That is a host with a **non-empty but frozen** single-layer status.

The shipped fix arms the stall only on a *frozen non-empty* `status_msg` (`if (stall_timeout and last_msg and status != "running" ...)`). This closes the *empty/coarse* sub-case only. It does **nothing** for the large-single-layer case — a healthy host frozen on `"c60621a45423: Pulling fs layer"` is byte-for-byte indistinguishable from a stuck one, so it still false-abandons.

Now compare what the synthesis says about this:

- **Message 1, "push back":** *"The realistic false-positive is the large-single-layer case, not literally-empty — but the fix is the same shape, so it's a framing nit, not a defense."* This is the distortion. The orchestrator concedes the *realistic* case is large-single-layer, then claims "the fix is the same shape" — but the fix does not touch that case. Calling the unfixed core of the #1 objection a "framing nit" downgrades a live defect to a wording quibble.
- **Message 1, "What I'd change" #1:** claims the fix "keeps it firing on the exact stuck hosts we saw (frozen `'Pulling fs layer'`) while **removing the false-abandon path**." These two clauses are contradictory: `"Pulling fs layer"` frozen *is* both the stuck signal and the healthy-slow signal. You cannot "keep firing on frozen `Pulling fs layer`" AND "remove the false-abandon path" — that is the entire point of the objection.
- **Message 2 table, row 1:** relabels the strongest objection narrowly as *"Empty/absent `status_msg` → false stall-abandon"* and marks it fixed. The strongest objection's teeth (the 27-min single-layer case) are split off into a footnote.

### What pulls this back from material: the residual IS disclosed

Message 2 ends with an explicit, prominent concession: *"One honest residual the red-team flagged and I did not fully close: a genuinely large single layer with a non-empty-but-frozen status on a slow host could still false-abandon."* That is exactly the red-team's core case, disclosed in plain language, marked provisional. So a reader of the full synthesis is told the top finding is not fully closed, and the verdict (`serious-but-fixable`) is preserved, not softened. Message 1's over-claim ("removing the false-abandon path") is contradicted by Message 2's own words — sloppy/self-serving in the moment, but self-corrected.

### The 1200s "margin" does not clear the red-team's own worst case

The synthesis presents the `900 → 1200s` bump as margin and lists the residual as "mitigated (20-min window...)." But the red-team's concrete example is **~27 min** (40 GB @ 200 Mbps, line 36). 27 min > 20 min, so the bump does **not** cover the stated worst case — the false-abandon still triggers at the red-team's own example. Presenting 1200s as adequate margin is spin; the honest statement is "still under the flagged worst case, relying on the download-aware sort to have steered away from the slow host by then" (which on a thin market may not hold — see below).

### A dropped hidden assumption the synthesis glosses

Red-team hidden assumption (line 63) and "would change my mind" (line 73): the tie-break can steer toward a **more expensive fast host with no rate backstop** because `--max-price` defaults to `None` (argparse line 1123), so the cost ceiling only exists if CI supplies it. The synthesis addresses the `inf`-determinism half (fixed, real — offer-id key verified in both diffs) but **silently drops** the `--max-price` half, and then asserts in Message 1: *"The tie-break itself holds up (provably respects VRAM-primary; unit math right)."* That blanket "holds up" papers over a flagged, unaddressed dependency. Low severity (the tie-break does factor `dph`, and it only reorders within a VRAM-equal class), but it was dropped, not argued.

### Fixes that genuinely close their finding (verified in code)

- **#5 (stall fires on running box awaiting ports)** — `status != "running"` guard added; test `test_poll_running_without_ports_not_stalled` present. Real.
- **#4 (all-`inf` → arbitrary order)** — final `o.get("id") or 0` key appended to the 5-tuple in both repos; determinism test present. Real.
- **#2 (ADR doc drift)** — ADR-0005 runtime-budget section updated with the loading-stall abandon and the "provisional" flag. Real (base-image only; vast_landing correctly noted as "no ADR there").
- **Empty-status false-positive (the fixed half of #1)** — `last_msg` gating verified; `test_poll_empty_status_msg_not_abandoned` present. Real.
- Test counts (110 / 862, +3 each) match the commit messages.

### Findings honestly carried, not buried

Weakness **#1 (flapping → no-op on multi-layer hangs)** and **#3 (tie-break outside `--max-price` envelope)** are listed in the synthesis's own "Findings NOT individually addressed" section with the arguments made and "no code change" stated. The #1 counter ("POLL_TIMEOUT still backstops a flapping hang → missed speedup, not a new failure") is technically sound and fair. These are disclosed, not laundered.

### `_brief.md`: no leading framing toward a soft verdict

The brief is adversarial in the correct direction. It says "Treat everything below as an unattributed claim that must earn its place," and point 3 pre-enumerates the exact attack surfaces (empty status_msg, flapping status_msg, `inf` blow-up, `--max-price` default, determinism). If anything it hands the red-team its findings, which makes the red-team look *less* independent — but that steers toward finding problems, not toward approval. No pre-loading toward `holds-up`. Clean.

### Trust boundary

Confirmed the red-team's note (line 7) that the diff/docstrings contain no embedded instruction or appeal-to-approval. I found none either. Nothing to report there.

---

## Net assessment

The synthesis preserves the verdict, ships four real and code-verified fixes, updates the ADR, and — crucially — explicitly discloses in Message 2 that the strongest objection's realistic case (large single layer, non-empty frozen) is **not** fully closed. That disclosure is what keeps this out of material-distortion territory.

But it does spin around that disclosure: Message 1 calls the unfixed core of the #1 finding a "framing nit, not a defense" and claims the fix "removes the false-abandon path" (contradicted by its own Message 2); the table relabels the strongest objection as the narrower "empty/absent" case and marks it fixed; the 1200s bump is sold as margin when the red-team's own 27-min example still exceeds it; and the `--max-price` hidden assumption is dropped under a blanket "tie-break holds up." These are self-serving framings, but each is either self-corrected or low-severity, and none reverses the verdict or hides the residual from the user.

AUDIT: minor-spin
