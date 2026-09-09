# ADR 0039 — A test suite does not fail on the artifact its own probe creates

- **Status:** Accepted
- **Date:** 2026-09-08
- **Decision owner:** Rob Ballantyne

## Context

Every engine suite (`vllm.d`, `sglang.d`, `vllm-omni.d`, `llama.d`) ships a
`contract_check.py` that posts a deliberately nonexistent model —
`NO_SUCH_MODEL = "__vast_contract_no_such_model__"` — to `/v1/chat/completions`.
`check_unknown_model` asserts the server *refuses* it rather than quietly serving
something else (ADR 0031).

On an engine that refuses, the refusal it writes to its log is the assertion
**succeeding**. The same suite's `10-<engine>-serving.sh` then greps that log for
`ERROR|CRITICAL` and calls `fail_later` on every hit, so the probe's own artifact
reds a correctly serving instance.

It stayed hidden because discovery order runs `10-` before `12-`: on a cold boot
the sentinel is not in the log yet, so the first pass is clean. It bites on the
**second** run — `runner.sh --manual` over SSH, which is exactly what the qa-fix
loop (ADR 0009) does on a held instance — where run *N*'s probe fails run *N+1*
and the reported failure names a model nobody asked for.

Reported 2026-09-02 from a live vLLM instance serving Qwen3.8-Flash-Next, where it
blocked a deploy test. **No log excerpt was captured**, so what is on the record is
a reproducible symptom on one engine, not the line that produced it. That gap is
the reason the scope question below decided the way it did.

The general form is not specific to this sentinel: **a test that provokes a failure
on purpose creates evidence that a sibling test will read as a defect.** Anything a
suite deliberately breaks — an unknown model, a malformed body, a refused auth —
lands in a log some other assertion is scanning.

## Options considered

- **Patch each exclusion list by hand.** Rejected: no mechanism prevents the next
  suite from repeating it, and nothing would find the case a human sweep missed.

- **Require the exclusion in every engine suite.** This was the first decision here
  and it was **wrong**, on evidence this repo had already written down.
  `docs/invariants.md` records, from measurement rather than documentation, that
  SGLang and llama.cpp answer the unknown-model probe **HTTP 200 and serve whatever
  they loaded**; both `contract_check.py` files declare `error-unknown-model` in
  `ENGINE["deviations"]` saying so. An engine that does not refuse logs nothing, so
  the exclusion is dead text, and an ERROR-severity rule demanding it teaches people
  to paste exclusions into scans that do not need them — the habit that makes a log
  scan worthless. Rejected on its own contradiction.

- **Make the probe not log.** Suppress engine logging around the probe, or pick a
  name the engine rejects earlier. Rejected: the logging behaviour is upstream's and
  varies per engine and version, so the assertion would become hostage to a log-level
  change. The probe *should* be visible — an operator reading the log should see the
  check ran.

- **Snapshot the log offset and scan only new lines.** Rejected, but not for the
  reason first given here. The original rejection said a persisted offset "makes a
  test's verdict depend on state from a previous invocation", which is backwards: the
  cumulative-log grep is *already* fully dependent on prior-run state, and an offset
  would remove that dependence. The real objections are that `10-` runs *before*
  `12-`, so a within-run offset changes nothing, and that a cross-run offset would
  also hide a genuine error that recurred. It remains the strongest rejected option
  and is the one to revisit if exclusions keep accumulating.

- **Scan only for the engine's own error taxonomy.** Rejected as disproportionate:
  four engines, four upstream-owned and unstable taxonomies. ADR 0031's reversal
  clause says to narrow an assertion, not widen the machinery.

- **Exclude the sentinel, gated by the suite's own declared contract.** Chosen.

## Decision

A suite that greps its engine log for `ERROR`/`CRITICAL` must exclude the sentinel
its own sibling test provokes — **where that engine is declared to refuse**.

**L093** enforces it. A `*.d` suite is in scope when its `contract_check.py` defines
`NO_SUCH_MODEL` and does **not** declare the `error-unknown-model` deviation. The
rule therefore reads the same declaration the suite asserts against, which is what
stops the two drifting. The deviations are self-expiring by design: the day an
engine starts refusing, its declaration becomes a violation, the entry goes, and
**this rule arms itself** with nobody remembering to.

In scope today: `vllm`, `vllm-omni`. Out of scope by declaration: `sglang`,
`llama-cpp`.

Scope is narrow in two further places:

- **Only the engine's own log.** `check_log_errors "ray" "$RAY_LOG"` scans a process
  the probe never reaches, identified by the label matching the suite stem.
- **Only a real exclusion counts.** `check_log_errors` reads `$3` and nothing else,
  so the sentinel must be an alternative of that single quoted argument. A sentinel
  in a fourth argument, spliced on without its `|`, or sitting elsewhere on the line
  is decorative, and is reported as such rather than passing a substring test.

A suite in scope with no label-matching call is itself a finding: the convention the
rule reads has broken and the engine log is no longer identifiable.

## Binding conditions

- The exclusion is the **sentinel string only**. It is unique to the probe, so it
  cannot mask a genuine error. Broad patterns (`.*model.*not found`) are not an
  acceptable way to satisfy L093 — they would hide the real substitution defect
  ADR 0031 asks this suite to detect.
- Excluding upstream log *noise* (a library logging a warning at ERROR level) is a
  separate judgement and stays outside L093. Such an exclusion must be anchored
  tightly enough to name the emitter, and added only where the line has actually been
  observed — not speculatively across sibling images.
- **`vllm-omni` is in scope by declaration, not by measurement.** Its `deviations`
  dict is `{}`, inherited when the suite was created by copying `vllm.d`, and its
  contract has not been run against a live multimodal instance. If it turns out not
  to refuse, the fix is to declare the deviation — which removes it from scope here
  automatically — not to weaken the rule.

## Known, and NOT addressed by this decision

`llama.cpp`'s malformed-body probe is the same defect shape and this ADR does not
close it. `check_malformed_body` posts a truncated body on every run, and
`docs/invariants.md` records llama.cpp answering **HTTP 500** where the spec says
4xx — declared with an explicitly weak bound, on the grounds that it fails loudly.
Failing loudly is precisely what puts a line in the log that `10-llama-serving.sh`
will read on the next `--manual` re-run.

It is left open deliberately rather than fixed blind: unlike the unknown-model
probe, a malformed body carries **no sentinel** to exclude, so any fix is either a
broad pattern (refused by the binding conditions above) or a change to what the probe
sends, and neither should be chosen without a log excerpt from a live llama.cpp
instance showing what is actually written. Whoever next holds a llama.cpp box under
the qa-fix loop should capture that and reopen this.

## Consequences

- Re-running a suite is idempotent with respect to the unknown-model probe on the
  engines that refuse. The qa-fix loop can re-run `runner.sh --manual` on a held
  instance without generating a failure it caused itself.
- A new engine suite copied from an existing one inherits a gated invariant rather
  than a latent bug, and inherits it **only if its declared contract says it refuses**.
- Accepted negative: one string is duplicated across the in-scope exclusion lists,
  because each image ships its own overlay copy of `check_log_errors`. The linter is
  what keeps the copies honest; consolidating them is a separate change.
- Accepted negative: the llama.cpp malformed-body case above stays open.

## What would reverse this

- A log excerpt from a live SGLang or llama.cpp instance showing an `ERROR`/`CRITICAL`
  line containing the sentinel. That would falsify `docs/invariants.md`'s measured
  table and both `deviations` entries — in which case those entries are what needs
  fixing, and L093 re-arms on its own once they go.
- `contract_check.py` stops probing with a sentinel model name, leaving nothing for
  the suite to excuse.
- `check_log_errors` is replaced by per-engine taxonomy matching, at which point the
  exclusion list — and this rule — has no subject.
