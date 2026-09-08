# ADR 0039 — A test suite does not fail on the artifact its own probe creates

- **Status:** Accepted
- **Date:** 2026-09-08
- **Decision owner:** Rob Ballantyne

## Context

Every engine suite (`vllm.d`, `sglang.d`, `vllm-omni.d`, `llama.d`) ships a
`contract_check.py` that posts a deliberately nonexistent model —
`NO_SUCH_MODEL = "__vast_contract_no_such_model__"` — to `/v1/chat/completions`.
`check_unknown_model` asserts the server *refuses* it rather than quietly serving
something else (ADR 0031). The engine logging that request as an error is the
assertion **succeeding**.

The same suite's `10-<engine>-serving.sh` then greps the same log for
`ERROR|CRITICAL` and calls `fail_later` on every hit. The probe's own artifact
therefore reds a correctly serving instance.

It stayed hidden because discovery order runs `10-` before `12-`: on a cold boot
the sentinel is not in the log yet, so the first pass is clean. It bites on the
**second** run — `runner.sh --manual` over SSH, which is exactly what the qa-fix
loop (ADR 0009) does on a held instance — where run *N*'s probe fails run *N+1*
and the reported failure names a model nobody asked for.

Surfaced 2026-09-07 while reviewing PR 275, which patched the vLLM suite by hand.
Sweeping for the shape found the identical exposure latent in all three siblings.

The general form is not specific to this sentinel: **a test that provokes a
failure on purpose creates evidence that a sibling test will read as a defect.**
Anything a suite deliberately breaks — an unknown model, a malformed body, a
refused auth — lands in a log some other assertion is scanning.

## Options considered

- **Patch each exclusion list by hand.** What PR 275 did. Rejected: it is how the
  defect got to four images. Three siblings were already wrong and nothing would
  have found the fourth. No mechanism prevents the next suite from repeating it.

- **Make the probe not log.** Use a model name the engine rejects before it
  reaches the logger, or suppress engine logging around the probe. Rejected: the
  engine's logging behaviour is upstream's and varies per engine and version;
  building on it makes the assertion hostage to a log-level change. The probe
  *should* be visible — an operator reading the log should see that the check ran.

- **Snapshot the log offset and scan only new lines.** Have `10-` record the byte
  offset and have later tests scan from there. Rejected as the wrong shape for the
  real ordering: `10-` runs *first*, so a within-run snapshot changes nothing, and
  a persisted cross-run offset makes a test's verdict depend on state from a
  previous invocation — worse to reason about than an exclusion, and it would also
  hide a genuine error that recurred.

- **Scan only for the engine's own error taxonomy** rather than the string ERROR.
  Rejected as out of proportion: four engines, four taxonomies, all upstream-owned
  and unstable. That is a larger redesign of `check_log_errors` than the defect
  justifies, and ADR 0031's reversal clause says to narrow an assertion, not widen
  the machinery.

- **Exclude the sentinel, and gate the exclusion in the linter.** Chosen.

## Decision

A suite that greps its engine log for `ERROR`/`CRITICAL` must exclude the
sentinels its own sibling tests deliberately provoke. **L093** enforces it: where a
`*.d` suite contains both a `contract_check.py` defining `NO_SUCH_MODEL` and a
`check_log_errors` call labelled with the suite stem, the exclusion argument must
contain that sentinel.

Scope is deliberately narrow in two places:

- **Only the engine's own log.** `check_log_errors "ray" "$RAY_LOG"` scans a
  process the probe never reaches. A rule that demanded the sentinel there would
  teach people to paste exclusions into scans that do not need them, which is the
  habit that makes a log scan worthless.
- **The engine log is identified by the label matching the suite stem**
  (`vllm.d` → `"vllm"`). If that convention breaks, the rule can no longer see the
  engine log, so **a suite with the sentinel but no label-matching call is itself a
  finding** rather than a silent pass.

## Binding conditions

- The exclusion is the **sentinel string only**. It is unique to the probe, so it
  cannot mask a genuine error. Broad exclusions (`.*model.*not found`) are not an
  acceptable way to satisfy L093 — they would hide a real substitution defect,
  which is the thing ADR 0031 asks this suite to detect.
- Excluding upstream log *noise* (a library that logs a warning at ERROR level) is
  a separate judgement and stays out of L093. Such an exclusion must be anchored
  tightly enough to name the emitter, and must be added only where the line has
  actually been observed — not speculatively across sibling images.

## Consequences

- Re-running an instance test suite is idempotent with respect to the contract
  probe. The qa-fix loop can re-run `runner.sh --manual` on a held instance
  without generating a failure it caused itself.
- A new engine suite copied from an existing one inherits a gated invariant rather
  than a latent bug: L093 fires at lint time, before a build.
- Accepted negative: one string is duplicated across four exclusion lists, because
  each image ships its own overlay copy of `check_log_errors`. The linter is what
  keeps the copies honest; consolidating them is a separate change.

## What would reverse this

- `contract_check.py` stops probing with a sentinel model name (the unknown-model
  assertion is dropped or restructured), leaving nothing for the suite to excuse.
- `check_log_errors` is replaced by per-engine error taxonomy matching, at which
  point the exclusion list — and this rule — has no subject.
